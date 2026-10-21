# ATLAS-666 — Registro y reemplazo de datasets desde el chat

## Propósito y alcance

Un usuario puede adjuntar un archivo al chat y pedir que el agente lo registre en el Resource
Registry. El sistema usa el Workspace autorizado de la solicitud y decide si debe crear un Dataset
o reemplazar el contenido de uno existente. En esta ruta el usuario **no proporciona**
`tenant_id`, `dataset_id` ni `upload_id`.

Este documento describe el comportamiento implementado para archivos CSV, JSON, JSONL, NDJSON,
Parquet y XLSX. La extensión debe estar habilitada en
`RESOURCE_REGISTRY_UPLOAD_ALLOWED_EXTENSIONS` y el MIME debe coincidir con la política del
formato. `resource_registry_replace_dataset` también conserva una ruta para un upload del Registry
ya verificado: allí se requieren `upload_id` y exactamente uno de `dataset_id` o
`workbook_group_id`.

> **Dos significados de “publicar”:** la ruta del chat crea un Dataset activo con una versión
> `READY` mediante los workers de registro. No pasa por el flujo separado de publicación manual
> de *Staged Resource Intake*. Si `RESOURCE_REGISTRY_OUTBOX_ENABLED` está activo, la creación de
> una versión `READY` también persiste un evento en el outbox; su entrega es otro proceso.

## Vista de extremo a extremo

```mermaid
sequenceDiagram
    actor Usuario
    participant Chat as ChatServiceView
    participant Router as Enrutador del agente
    participant BIF as resource_registry_replace_dataset
    participant Import as ChatDatasetImportService
    participant Store as Almacenamiento Registry
    participant DB as Base de datos Registry
    participant Worker as Worker de registro

    Usuario->>Chat: Mensaje + archivo en multipart content
    Chat->>Store: Copia temporal del chat para BIFs generales
    Chat->>Router: Mensaje + request.FILES + sesión/Workspace
    Router->>BIF: Orden explícita; argumentos vacíos {}
    BIF->>Import: Archivo actual + ámbito autorizado
    Import->>DB: Buscar identidad por nombre en el Workspace
    Import->>Store: Guardar y verificar upload propio del Registry
    Import->>DB: Crear RegistrationSubmission + job
    BIF-->>Router: registration_id, action, SUBMITTED
    Router->>BIF: Consultar estado con registration_id
    Router-->>Usuario: Estado actual; no promete finalización prematura
    Worker->>Store: Leer upload verificado
    Worker->>DB: Crear Dataset/versión o detectar ausencia de cambios
    Worker-->>DB: Guardar resultado completed/failed
```

La **copia temporal del chat** y el **upload controlado del Registry** son objetos distintos.
El BIF lee el archivo adjunto del `request.FILES` del turno actual y crea el segundo; el nombre
insertado en el texto del chat no sustituye los bytes del archivo.

## Paso a paso con `datos.csv`

### 1. El usuario envía archivo e instrucción

La solicitud del chat lleva `datos.csv` en el campo multipart `content`, junto con una orden
explícita, por ejemplo:

> Register or update the attached file in the Resource Registry for my current Workspace. If it
> is new, create it. If it already exists, replace its contents and check the status.

[`ChatServiceView`](../BotServer/botserver_api.py) lee el archivo, valida el tipo admitido por
la ruta del chat, agrega una línea `Uploaded file(s): datos.csv` al mensaje y guarda una copia
temporal para los BIFs generales. El archivo sigue disponible en `request.FILES['content']` para
la importación del Registry.

### 2. El enrutador decide si hay una orden de importación

[`ExplorationRoutingPolicy`](assistant/exploration_routing.py) reconoce
órdenes explícitas de registrar, importar, subir, reemplazar o actualizar un archivo/Dataset. La
línea de nombres que agregó el servidor no cuenta como instrucción del usuario. Preguntas
hipotéticas, instrucciones negativas y solicitudes de actualizar un gráfico no activan esta ruta.

[`DataRegistryExplorationRoutingAdapter`](assistant/runtime_adapter.py)
exige un adjunto del **turno actual**, resuelve el Workspace autorizado y comprueba que la Persona
tenga asignado el BIF. Con varios Workspaces sin selección, pide elegir uno antes de escribir.
Para una orden admitida llama a `resource_registry_replace_dataset` con `{}` por el despachador
normal de BIFs; no depende de que el modelo invente o elija identificadores.

### 3. El BIF fija el ámbito y selecciona el archivo

[`resource_registry_replace_dataset`](../BotServer/builtin_functions/resource_registry_functions.py)
obtiene el Namespace del Workspace activo de la solicitud y aplica la admisión y la política de
capacidades del Registry. Un `tenant_id` opcional solo puede confirmar ese Namespace; no puede
cambiar el Workspace. Si se adjuntan varios archivos y no se especifica un `filename` exacto,
devuelve `chat_file_selection_required` en lugar de escoger arbitrariamente.

En la ruta sin `upload_id`, el BIF toma el archivo actual y llama a
[`ChatDatasetImportService`](services/chat_dataset_import_service.py).
Un archivo mencionado en mensajes anteriores no basta para iniciar una importación.

### 4. Se identifica el destino dentro del Workspace

El servicio busca un Dataset de archivo, no archivado y perteneciente al mismo Organization,
Namespace y Workspace. Para archivos planos compara el nombre del archivo y el slug derivado
del nombre **con extensión**; también contempla el slug legado. Para XLSX busca un
`WorkbookIngestionGroup` por `source_file_name` y comprueba sus Datasets vinculados.

| Resultado de la búsqueda | Decisión |
| --- | --- |
| Ninguna coincidencia | Registrar un Dataset nuevo; para XLSX, un grupo y sus hojas. |
| Una coincidencia autorizada | Reemplazar el Dataset o grupo existente. |
| Varias coincidencias | Detenerse con `dataset_match_ambiguous`. |
| Coincidencia sin permiso de escritura | Detenerse con error de autorización. |

La identidad automática depende del **nombre del archivo en ese Workspace**. Renombrar el
archivo puede hacer que se trate como uno nuevo. Un `dataset_id` o `workbook_group_id` descubierto
por el agente, si se proporciona, se usa como comprobación adicional y debe coincidir con el
destino detectado.

### 5. Se crea un upload verificado del Registry

El servicio comprueba el permiso para enviar registros y, según la decisión, los permisos para
crear o modificar Datasets. Valida nombre, extensión, MIME y tamaño; lee el archivo por bloques,
calcula SHA-256 y lo pasa a
[`ServerAcquiredUploadService`](uploads/server_acquisition_service.py).
Este servicio guarda el objeto en el proveedor configurado, verifica sus metadatos y deja un
`LargeFileUploadSession` en `COMPLETED`. Si falla almacenamiento o integridad, compensa el
objeto fallido y no envía el registro.

### 6. Se envía el trabajo asíncrono

[`RegistrationSubmissionService`](registration_submissions/services.py)
verifica que el upload sea `COMPLETED`, pertenezca al actor y al Workspace admitido, y que el
destino sea escribible. Crea un `RegistrationSubmission` con un `registration_id` y un job sin
procesar el archivo en el hilo HTTP:

| Formato | Job |
| --- | --- |
| CSV, JSON, JSONL/NDJSON, Parquet | `DatasetRegistrationJob` |
| XLSX | `WorkbookIngestionJob` |

El BIF devuelve `action=register` o `action=replace`, `registration_id`, `job_type` y el estado
inicial `SUBMITTED`. Es **aceptación del trabajo**, todavía no publicación completa.

Campos principales de una respuesta de primera carga (UUID ilustrativo):

```json
{
  "success": true,
  "data": {
    "registration_id": "<uuid>",
    "status": "SUBMITTED",
    "job_type": "DatasetRegistrationJob",
    "action": "register",
    "dataset_id": null
  }
}
```

### 7. El worker valida y publica

Los comandos `run_dataset_registration_jobs` y `run_workbook_ingestion_jobs` promueven y ejecutan
jobs fuera de la solicitud del chat. Su frecuencia real depende del despliegue. El worker vuelve
a comprobar el upload, su procedencia y, para reemplazos, los permisos y el Workspace vigentes.
Si se revocó el acceso entre el envío y la ejecución, el job falla sin reemplazar el Dataset.

El worker de archivos planos materializa el upload verificado y llama a
[`DatasetRegistrationFacade`](application/dataset_registration.py).
Esta valida el archivo y el esquema, crea o reutiliza la identidad del Dataset, guarda una
`DatasetVersion` `READY`, esquema, perfil agregado y artefacto fuente, y genera un snapshot
canónico Parquet. El Dataset queda `ACTIVE`. Esta es la publicación del contenido para consultas
del Registry.

### 8. El sistema decide si debe crear una versión

En un reemplazo plano, el hash de contenido se compara con el de la **última versión**. Si es
igual, se reutiliza esa versión y el resultado incluye `no_changes_detected=true`. Si cambia,
se crea la versión siguiente (por ejemplo, v2), sin sobrescribir v1. Las versiones `READY` son
inmutables y las anteriores siguen disponibles para auditoría o selección explícita. Si se
reintroducen los bytes de una versión antigua que ya no es la última, se crea una nueva versión.

Para `datos.csv`:

| Envío | Resultado después del worker |
| --- | --- |
| Primera carga, sin coincidencia | Dataset nuevo, v1 `READY`. |
| Mismo nombre, contenido distinto | Mismo Dataset, v2 `READY`; v1 permanece. |
| Mismo nombre, contenido igual a v2 | `no_changes_detected=true`; sigue en v2. |

### 9. Particularidad de XLSX

La primera carga registra un grupo y un Dataset por hoja. Al reemplazar, el
[`WorkbookReplacementService`](services/workbook_replacement_service.py)
mantiene el grupo: crea versiones para hojas modificadas, conserva hojas sin cambios, crea
Datasets para hojas nuevas y archiva los Datasets de hojas retiradas. Procesa el conjunto bajo
una transacción con bloqueo del grupo. Ante una falla, revierte los cambios de base de datos e
intenta limpiar los snapshots nuevos. El resultado incluye `archived_dataset_ids` y
`no_changes_detected` cuando no cambió ninguna hoja ni se archivó alguna.

### 10. El agente consulta y comunica el resultado

Después del envío, el enrutador llama una vez a
`resource_registry_get_replacement_status(registration_id=...)` si la Persona tiene ese BIF.
La consulta solo ve registros del actor, Organization, Workspace y Namespace actuales. El estado
del **job** puede ser `submitted`, `queued`, `running`, `completed`, `failed` o `cancelled`;
un workbook también puede devolver `completed_with_skips`.

Si el job sigue en curso, el agente devuelve el `registration_id` e indica que la finalización
está pendiente. Si la consulta devuelve `completed`, informa creación/reemplazo o ausencia de
cambios. Para un `completed_with_skips` de XLSX se debe inspeccionar el resultado detallado del
BIF de estado. **No hay espera continua ni polling automático dentro de esa respuesta**: para
conocer un resultado posterior hay que consultar el estado con el mismo `registration_id`.

Campos principales de una respuesta de estado tras un reemplazo completado:

```json
{
  "success": true,
  "data": {
    "registration_id": "<uuid>",
    "status": "completed",
    "result": {
      "dataset_id": "<uuid>",
      "dataset_version_id": "<uuid>",
      "no_changes_detected": false
    },
    "error_code": null
  }
}
```

## Seguridad, concurrencia y fallos

| Control | Efecto |
| --- | --- |
| Sesión, Organization y Workspace del servidor | El texto del usuario no establece el ámbito de escritura. `RESOURCE_REGISTRY_SCOPE_POLICY` sigue aplicando. |
| Asignación de BIF a Persona y capacidad `dataset:write` | Solo una Persona habilitada puede iniciar el flujo. |
| Permisos de upload, registro y Dataset | Se comprueban al enviar; los reemplazos se vuelven a comprobar al ejecutar. |
| Identidad por Workspace y comprobación de destino | Impide reemplazar un Dataset de otro Workspace o elegir uno ambiguo. |
| Idempotencia y bloqueos | Repetir el BIF dentro del mismo turno reutiliza la solicitud; dos primeras cargas paralelas del mismo nombre no crean dos Datasets. Los reemplazos se serializan sobre el destino. |
| Respuesta de errores | No se anuncia éxito cuando el BIF o el job fallan; el estado conserva un `error_code` sanitizado. |

Errores útiles para diagnóstico: `chat_file_not_attached`, `chat_file_selection_required`,
`dataset_match_ambiguous`, `dataset_import_in_progress`, `dataset_not_found`,
`authorization_denied`, `upload_unsupported_file_format` y
`upload_extension_content_type_mismatch`. Un `registration_id` permite seguir un job aceptado;
no sirve para consultar trabajos ajenos.

## Referencias y comprobación

- Contrato y catálogo de BIFs:
  [`resource_registry_functions.py`](../BotServer/builtin_functions/resource_registry_functions.py),
  [`manifest.py`](bifs/manifest.py).
- Creación, versiones y publicación:
  [`dataset_registration.py`](application/dataset_registration.py),
  [`dataset_registry_service.py`](services/registry/dataset_registry_service.py).
- Pruebas del recorrido:
  [`test_chat_dataset_import.py`](tests/test_chat_dataset_import.py),
  [`test_agent_dataset_replacement.py`](tests/test_agent_dataset_replacement.py),
  [`test_resource_registry_assistant_exploration_runtime_adapter.py`](../tests/botserver/test_resource_registry_assistant_exploration_runtime_adapter.py).

La validación funcional de este documento es una inspección del código y de sus pruebas. El
entorno donde se despliegue debe tener aplicadas las migraciones, el proveedor de almacenamiento
configurado y ambos workers en ejecución para que `SUBMITTED` avance hasta un estado terminal.
Para una ejecución local puntual de los workers, los comandos son:

```bash
uv run --env-file .env python manage.py run_dataset_registration_jobs --once
uv run --env-file .env python manage.py run_workbook_ingestion_jobs --once
```
