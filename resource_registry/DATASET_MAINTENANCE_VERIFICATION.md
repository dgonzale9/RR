# Verificación de mantenimiento de datasets

Fecha: 2026-10-29. Alcance: backend, REST y Django Admin. No se modificaron
`apps/uniai-site`, proxies, SSO ni asignaciones reales de staff. No se activó ningún
piloto real, no se ejecutaron migraciones sobre datos de producción y no se creó un commit.

Los resultados iniciales son un registro histórico de la entrega. Las correcciones
posteriores al final del documento delimitan el alcance vigente; la guía de operación
refleja la implementación actual.

## Evidencia de aceptación

| Área | Evidencia |
| --- | --- |
| Staff separado de permisos de negocio | `test_dataset_management_admin`, `test_dataset_management_api`: lector sin permiso global, manager sin staff en API, Admin deniega no staff |
| Tenancy y permisos revocados | `test_dataset_management`, `test_dataset_changes`, `test_dataset_management_api`: host obligatorio, destinos ajenos, grant insuficiente y revocación antes de publicar |
| Formularios y selectores | `test_dataset_management_admin`: páginas renderizadas, selección de destino, POST con objetivo de hoja inexistente rechazado sin cambiar la propuesta |
| Alta desde Admin | Carga por bloques → complete → inspección → propuesta → worker → publicación, sin `manage_stagedresourceintake` |
| Versiones e identidad | Archivo renombrado conserva ID; candidatos privados; contenido idéntico/histórico no duplica versiones; no se expone un READY sin artefactos utilizables |
| Compatibilidad y restauración | Colaborador publica compatible; eliminación de columnas exige manager; esquema, perfil, contexto, consulta DuckDB y export plan siguen la referencia correcta |
| Excel | Hoja inválida bloquea el conjunto; exclusión explícita; cambio de nombre mantiene dataset; hoja nueva; archivado de ausente; fallo de publicación revierte datasets, mappings y outbox |
| Concurrencia y reintentos | Revisión de metadatos bloquea publicación obsoleta; dos publicaciones concurrentes crean una versión; rollback y retry después de fallo; almacenamiento ausente conserva vigente |
| Archivado | Archivado manual no se revierte por ingesta; recuperación comprueba artefactos; pruebas existentes de retirada de destino/publicación compartida incluidas |
| Migración y piloto | Migraciones sobre PostgreSQL reconstruido; backfill histórico probado; desactivación bloquea mutaciones y mantiene lecturas y protección contra updates por nombre |
| Coste de lectura | Comparaciones de cantidad de consultas con catálogos e historiales pequeños/grandes; artefactos actuales sin duplicación por joins |

## Resultados ejecutados

- **447 pruebas aprobadas** en la suite final de aceptación y consumidores, después
  de corregir B1 (`/private/tmp/registry-b1-final-suite.log`). Incluye los seis módulos nuevos,
  lecturas, consultas, contratos BIF, productores externos, intelligence jobs,
  retención, retirada de destinos, persistencia de inteligencia y artefactos.
- **40 pruebas de aceptación aprobadas** con `--create-db --reuse-db`, aplicando todas
  las migraciones a una base PostgreSQL desechable
  (`/private/tmp/registry-acceptance-final.log`). Las comprobaciones posteriores de
  selección de versión y las tres regresiones de B1 están incluidas en las 447 anteriores.
- **123 aprobadas y un fallo preexistente** en la verificación adicional de Admin,
  productores y límites de upload (`/private/tmp/registry-corrected-regressions.log`).
  Este resultado se solapa parcialmente con la suite de 447; no debe sumarse como
  una cantidad de pruebas únicas.
- Ruff, Black aplicado al alcance modificado, `node --check` y `git diff --check`:
  correctos (`/private/tmp/registry-quality-final.log`). La corrección posterior B1
  también pasa Ruff, Black y `git diff --check` (`/private/tmp/registry-b1-quality.log`).
- `manage.py check`: sin incidencias. `makemigrations --check --dry-run`: sin cambios
  pendientes. La consulta opcional al historial desde el sandbox fue denegada;
  la aplicación real de migraciones se verificó con PostgreSQL de pruebas autorizado.
- Mypy: **652 diagnósticos preexistentes en 22 archivos**, sin diagnósticos nuevos
  al comparar mensajes normalizados contra HEAD limpio con los mismos archivos y
  opciones. Repetido tras B1 con el mismo resultado (`/private/tmp/registry-b1-types.log`
  y `/private/tmp/registry-b1-types-comparison.log`). El chequeo global de tipos no está
  verde y no se presenta como tal.
- OpenAPI generado y validado; rutas de sesión/token y `Restore.version_id` obligatorio
  comprobados. El generador conserva errores de vistas antiguas sin serializers y
  advertencias de nombres de operación. No hay errores de generación atribuidos a las
  vistas nuevas (`/private/tmp/registry-openapi.log`).

Se usó `uv run --env-file .env --no-sync`, `AZ_STORAGE_ACCOUNT_NAME` vacío para el
proceso de pruebas y almacenamiento local. Las pruebas de base de datos se ejecutaron
secuencialmente por base. No se contactaron proveedores reales para validar estos flujos.

Comando de aceptación reproducible:

```bash
uv run --env-file .env --no-sync python -c '
import os, sys
os.environ["AZ_STORAGE_ACCOUNT_NAME"] = ""
import pytest
sys.exit(pytest.main(sys.argv[1:]))
' -q --reuse-db \
  resource_registry/tests/test_dataset_management.py \
  resource_registry/tests/test_dataset_changes.py \
  resource_registry/tests/test_dataset_management_api.py \
  resource_registry/tests/test_dataset_management_admin.py \
  resource_registry/tests/test_dataset_maintenance_flows.py \
  resource_registry/tests/test_dataset_management_integrity.py
```

## Fallos preexistentes separados

Se extrajo HEAD limpio a una carpeta temporal y se utilizó una base de pruebas distinta.
La comparación reprodujo **22 fallos**: 18 del registro antiguo (configuración Azure y
dobles de almacenamiento), dos de dobles de idempotencia y dos del Admin operativo de
intakes (HTTP 403). Registro: `/private/tmp/registry-preexisting-baseline.log`.

La prueba adicional `test_backend_intake_upload::test_admin_upload_requires_permission_and_csrf`
también devuelve 403 en HEAD limpio (`/private/tmp/registry-upload-admin-baseline.log`).
Estos fallos no se ocultaron ni se marcaron como skip. Los dobles afectados por la nueva
dependencia de selección de versión se actualizaron manteniendo las aserciones de negocio;
el comportamiento se prueba además con modelos reales y PostgreSQL.

## Hallazgo del gate y corrección

La primera evaluación independiente devolvió `DO NOT CLOSE` por B1: una propuesta CSV
con correspondencias de hojas podía autorizarse como edición y prepararse como un alta,
permitiendo crear sin el permiso del destino.

Se reprodujo antes de corregirlo con tres pruebas fallidas
(`/private/tmp/registry-b1-red.log`). Ahora se rechazan operaciones de hojas para fuentes
no Excel al crear y procesar; al publicar se comprueba que los objetivos efectivos coincidan
con la selección autorizada y se exige creación para cada alta. Las propuestas antiguas
incompatibles tampoco pueden publicarse. Las tres regresiones verifican que no se creen
datasets, versiones ni eventos en esos casos; junto con los flujos de integridad y Admin
pasan **21 pruebas** (`/private/tmp/registry-b1-green.log`).

## Comprobación visual y límites

Se inspeccionaron en Chrome las seis pantallas renderizadas por Django: catálogo, ficha,
carga, actualizaciones, revisión de hojas y colaboradores. Se comprobaron estructura,
etiquetas, codificación, controles habilitados y enlaces. Los HTML temporales están en
`/private/tmp/registry-admin-visual/`. Se corrigieron título/codificación del catálogo,
controles de revisión prematuros y el aviso de error vacío.

La interacción completa de carga y publicación se verificó mediante las vistas y servicios
reales en pruebas de Django. La inspección del navegador usó HTML servido localmente;
no constituye una prueba de SSO ni una sesión navegada contra producción. La validación
del almacenamiento real y del despliegue de workers corresponde al piloto descrito en
[DATASET_MAINTENANCE.md](DATASET_MAINTENANCE.md).

El gate independiente `$bounded-final-closure-assessment` se invoca después de esta
verificación. Su veredicto se entrega por separado; este documento no lo anticipa.

## Corrección posterior: idioma de Django Admin

Por indicación del usuario, los textos de mantenimiento del Admin se cambiaron a inglés:
formularios, filtros, columnas, seis plantillas, mensajes de confirmación/validación y
progreso de carga en JavaScript. La guía conserva el español y usa los nombres reales
en inglés de los controles. No se modificaron reglas de negocio ni contratos.

- **11 pruebas del Admin aprobadas**, incluidas las seis pantallas renderizadas y el
  recorrido de carga/publicación (`/private/tmp/registry-admin-english-tests.log`).
- Ruff, Black, `node --check` y `git diff --check` correctos
  (`/private/tmp/registry-admin-english-quality.log`).
- Mypy del archivo Python modificado: sin incidencias
  (`/private/tmp/registry-admin-english-types.log`).
- Revisión de los textos renderizados y comparación estructural contra las copias
  anteriores: lógica Python, controles, enlaces y bindings de templates preservados.

Al ser un cambio de contenido reversible, se reutilizaron las pruebas existentes;
no se añadieron pruebas que solo duplicaran las cadenas traducidas. Esta corrección
se somete a un gate independiente acotado al idioma y la preservación del comportamiento.

## Corrección posterior: procesamiento sin comando manual

Ante la petición de eliminar el arranque manual de `run_dataset_changes` del recorrido
de actualización, el Admin activa la inspección de su carga y el servicio compartido
activa la preparación de propuestas mediante el ejecutor de baja prioridad existente.
Las activaciones se registran después del commit, son acotadas y agrupan repeticiones.
Cada tarea reclama únicamente su propuesta o intake; publicar sigue siendo explícito.

- Dos pruebas reprodujeron primero la falta de activación automática
  (`/private/tmp/registry-auto-red.log`).
- **81 pruebas aprobadas**: automatización, los seis módulos de mantenimiento, inspecciones
  existentes, formatos CSV/Excel/Parquet/JSON y autorización de intakes
  (`/private/tmp/registry-auto-final-tests.log`). Esta cifra incluye y sustituye la primera
  batería de 40 pruebas; no se suma a la suite de la entrega original.
- El recorrido Admin usa el envío real de tareas con entrega controlada por la prueba,
  sin invocar workers manualmente. Otra prueba usa el ejecutor real, PostgreSQL y archivos
  locales: la creación devuelve el control mientras la preparación está bloqueada, y
  la tarea termina sin publicar ninguna versión.
- Se probaron rollback sin ejecución, recuperación de activaciones perdidas, capacidad
  limitada, repeticiones, revisión de objetivos, fallo de admisión del ejecutor, reintento,
  cancelación, permisos revocados y piloto desactivado. Una inspección no reclama cargas
  ajenas, y los comandos existentes conservan su comportamiento de procesamiento por lotes.
- Ruff, Black, `node --check` y `git diff --check` correctos
  (`/private/tmp/registry-auto-quality.log`). Mypy: sin incidencias en los nueve archivos
  Python de esta corrección (`/private/tmp/registry-auto-types.log`).

El ejecutor vive dentro del proceso Django. PostgreSQL conserva los trabajos, pero una
activación perdida o rechazada por capacidad necesita una nueva consulta autorizada de
progreso para reintentarse. Los trabajos que estaban en ejecución respetan los plazos de
recuperación existentes. El comando anterior queda disponible para operación sin usuarios
conectados; no es requisito del Admin. No se añadió infraestructura ni se desplegaron
servicios externos. Esta corrección requiere su propio gate final independiente.

## Corrección posterior: retirada de gestión de colaboradores

Por petición explícita del usuario se retiraron la pantalla de gestión de colaboradores,
sus enlaces y ruta del Admin, el template, el servicio de asignación/revocación y el
serializer, vista y rutas REST específicos de principals bajo sesión y token. La guía
ya no anuncia esa capacidad. La pantalla que aparece en la inspección visual histórica
de esta entrega ya no forma parte de la implementación.

Se conservan las ACL de organización/workspace, los permisos Guardian existentes y sus
comprobaciones, la edición del responsable y las demás operaciones de mantenimiento.
No se borraron asignaciones persistidas, datos ni actividad histórica. No se requieren
migraciones y no se modificó el frontend.

- Primero se reprodujeron **cinco fallos esperados**: enlaces en catálogo/ficha y rutas
  de Admin, sesión y token aún presentes (`/private/tmp/registry-remove-collaborators-red.log`).
- **58 pruebas aprobadas** de mantenimiento, Admin, REST y procesamiento automático,
  incluidas las regresiones de rutas retiradas y las cinco pantallas restantes renderizadas
  (`/private/tmp/registry-remove-collaborators-tests.log`).
- Ruff, Black y `git diff --check` correctos
  (`/private/tmp/registry-remove-collaborators-quality.log`). Mypy sin incidencias en los
  siete archivos Python modificados (`/private/tmp/registry-remove-collaborators-types.log`).
- Django check sin incidencias. OpenAPI comprobado bajo ambos prefijos: no contiene
  las rutas ni el schema retirados y conserva los endpoints de datasets. Persisten las
  dos advertencias conocidas de colisión de nombres de operación del catálogo, resueltas
  automáticamente por el generador (`/private/tmp/registry-remove-collaborators-schema.log`).

Esta retirada explícita sustituye el requisito original de administrar colaboradores
desde la implementación y se somete a su propio gate final independiente.

## Corrección posterior: integridad de candidatos en Azure

La propuesta local `1a195b07-5ad5-4426-b667-3486abec6e88` fallaba con `candidate_changed`.
El diagnóstico leyó únicamente su candidato en Azure: los 114681 bytes y su SHA-256
coincidían con la evidencia preparada. Azure devolvía MD5 como `bytearray` de 16 bytes,
que la publicación comparaba incorrectamente con el SHA-256 hexadecimal de 64 caracteres.

Ahora el worker conserva el ETag del objeto preparado. La publicación comprueba tamaño
y ETag para esos candidatos; mantiene SHA-256 para el proveedor local y verifica mediante
descarga temporal el SHA-256 de propuestas remotas antiguas sin ETag. Los checksums ausentes
no omiten la comprobación. No cambian modelos, migraciones ni contratos REST públicos.

- Se reprodujeron cinco fallos antes de corregirlos y una regresión local ya aprobaba
  (`/private/tmp/registry-candidate-integrity-red.log`). Las nuevas pruebas usan el adaptador
  Azure real con I/O del SDK simulado: datos intactos con MD5 binario, candidatos nuevos y
  antiguos, alteraciones del mismo tamaño sin checksum del proveedor, fallo de descarga
  y comprobación local. También verifican idempotencia y que ETag evita volver a descargar
  los candidatos nuevos durante la publicación.
- **82 pruebas aprobadas** con el código final: integridad de candidatos, adaptadores de
  almacenamiento y los siete módulos de mantenimiento y procesamiento automático
  (`/private/tmp/registry-candidate-integrity-tests.log`). Ruff, Black y `git diff --check`
  correctos (`/private/tmp/registry-candidate-integrity-quality.log`); mypy sin incidencias
  en los tres archivos Python nuevos o modificados
  (`/private/tmp/registry-candidate-integrity-types.log`).
- La propuesta indicada se reintentó mediante el servicio de negocio y el procesamiento
  automático. Pasó de `FAILED`, revisión 0, a `READY`, revisión 1, con el mismo contenido,
  ETag registrado y comprobación de integridad correcta. **No se publicó ninguna versión**
  (`/private/tmp/registry-candidate-integrity-recovery.log`). Este reintento sí usó el
  almacenamiento Azure configurado para la propuesta; las pruebas automatizadas usan
  PostgreSQL aislado, almacenamiento local y blobs simulados.

La comprobación de propuestas remotas antiguas sin ETag puede requerir descargar el archivo
al publicar. Los candidatos recién preparados usan la comprobación de ETag y tamaño.
Esta corrección se somete a un gate final independiente y preserva los cambios ajenos
presentes en el worktree.

## Corrección posterior: estado de intakes publicados desde mantenimiento

Los intakes `1e4ad929-9e18-4181-b238-b8bb165db2d8` y
`8dcca682-7f63-489e-948b-a88885615932` permanecían en `READY_FOR_PUBLICATION`,
aunque sus propuestas estaban `PUBLISHED` y tenían versiones READY con artefactos
que identificaban la propuesta. Faltaba sincronizar el intake al confirmar la publicación.

La publicación de datasets sincroniza ahora estado, fecha y vencimiento dentro de su
transacción. Una repetición autorizada también puede reconciliar publicaciones anteriores
sin duplicar versiones ni eventos. El servicio de intakes conserva la fuente publicada
referenciada por propuestas, permite reutilizarla en el flujo anterior hacia otro destino
y restaura correctamente `PUBLISHED` si esa otra publicación falla. Las cargas canceladas
sin publicar conservan la limpieza existente. No se crean publicaciones ni jobs ficticios
del flujo anterior, ni se modifican modelos, migraciones, permisos o frontend.

- TDD: se reprodujeron los fallos de estado, repetición y reutilización entre flujos
  (`/private/tmp/registry-intake-status-red.log`). Ese primer pase también encontró una
  selección de hojas incompleta en el fixture nuevo, corregida antes de validar Excel.
  Una prueba adicional reprodujo la necesidad de conservar la limpieza al cancelar
  (`/private/tmp/registry-intake-status-cleanup-red.log`).
- **91 pruebas aprobadas** con PostgreSQL aislado y almacenamiento local o simulado:
  `/private/tmp/registry-intake-status-tests.log`. Cubren sincronización, Admin filtrado
  por Published, idempotencia, permisos revocados, rollback, almacenamiento fallido,
  Excel, estados sin publicación, cancelación y compatibilidad con intakes anteriores.
- Ruff, Black y `git diff --check` correctos; Django check sin incidencias
  (`/private/tmp/registry-intake-status-quality.log`,
  `/private/tmp/registry-intake-status-check.log`). Mypy conserva ocho diagnósticos
  preexistentes de settings en `intake/services.py`; no introduce ninguno. Se compararon
  con una copia anterior al cambio (`/private/tmp/registry-intake-status-types.log`,
  `/private/tmp/registry-intake-status-types-baseline.log`,
  `/private/tmp/registry-intake-status-types-comparison.log`).
- Reparación local mediante el servicio, comprobando primero las propuestas y sus
  artefactos: ambos intakes pasaron a `PUBLISHED` con sus fechas originales. Se conservaron
  el dataset, su revisión y vigente, las dos versiones, los dos artefactos y los eventos.
  Una segunda sincronización no realizó cambios; no quedan propuestas publicadas con
  intakes desincronizados en esa base (`/private/tmp/registry-intake-status-recovery.log`).
- Comprobación en el navegador autenticado: se recargó la URL reportada
  `/admin/resource_registry/stagedresourceintake/?o=-5`; las dos primeras filas muestran
  **Published**, sin vencimiento y sin limpieza pendiente. No se publicaron archivos
  nuevos ni se contactó almacenamiento externo durante esta reparación.

El estado agregado del intake no sustituye el detalle de cada propuesta o destino.
Esta corrección acotada se somete a su propio gate final independiente.

## Corrección posterior: publicaciones de destino para propuestas de mantenimiento

La sincronización del estado del intake no enlazaba el flujo nuevo con
`StagedResourcePublication`. Se integra ahora la publicación real por intake/destino,
con actor y fecha originales, vínculos a las propuestas y asignaciones a las versiones
exactas mediante `StagedResourcePublicationDataset`. La migración aditiva 0008 añade
`DatasetChange.publication`, nullable para propuestas anteriores. El resultado de una
propuesta con intake incorpora `versions` con identificadores exactos de dataset y versión.

La asignación de publicaciones se serializa bloqueando el intake y la publicación antes
de sus datasets, siguiendo el orden de retirada de destinos. Publicación, versiones,
asignaciones, propuesta e intake se confirman juntos. Se reutilizan destinos activos,
se rechazan retiradas pendientes y registros antiguos en curso, y los replays no reactivan
asignaciones retiradas. La retirada reconcilia tanto las versiones del job anterior como
las de propuestas posteriores. La ruta antigua responde 409 `INTAKE_ALREADY_PUBLISHED`
ante una publicación de mantenimiento ya existente, sin crear otro job.

El Admin muestra **Published by**, **Maintenance updates**, **Published versions** y
**Dataset maintenance: Published**. Las propuestas y versiones se precargan; la regresión
del inline compara una y tres publicaciones sin crecimiento por fila en consultas.

- TDD inicial: siete fallos esperados por ausencia de publicaciones, enlaces y presentación;
  dos casos de rechazo/rollback ya aprobaban (`/private/tmp/registry-publication-links-red.log`).
- Suite ampliada: **133 pruebas aprobadas y dos fallos preexistentes**
  (`/private/tmp/registry-publication-links-tests.log`). Los fallos son
  `test_change_form_shows_actions_only_to_intake_operators` y
  `test_cancelled_intake_hides_and_rejects_destination_publication`, ambos HTTP 403 y ya
  reproducidos en HEAD limpio en `/private/tmp/registry-preexisting-baseline.log`.
- Pase previo al gate: **13 regresiones nuevas** aprobadas, incluidos concurrencia entre
  propuestas distintas, Excel atómico, permisos revocados, ámbitos workspace/organización,
  compatibilidad con evidencia anterior, retirada compartida y reconciliación histórica
  (`/private/tmp/registry-publication-links-final-tests.log`).
- Ruff, Black y `git diff --check` correctos
  (`/private/tmp/registry-publication-links-quality.log`). Django check sin incidencias y
  `makemigrations --check --dry-run` sin cambios pendientes
  (`/private/tmp/registry-publication-links-schema.log`). Mypy conserva 67 diagnósticos
  anteriores en Admin/intakes y ninguno nuevo; se comparó contra las copias previas de
  los servicios y Admin (`/private/tmp/registry-publication-links-types*.log`).
- La migración 0008 se aplicó localmente. Las dos propuestas publicadas se reconciliaron
  mediante `DatasetChangeService.publish`, sin I/O de almacenamiento. El intake
  `1e4ad929-9e18-4181-b238-b8bb165db2d8` quedó enlazado a la versión 1 mediante publicación
  `2ee93322-ecd0-4962-9499-91118b36b5e0`; el intake
  `8dcca682-7f63-489e-948b-a88885615932`, a la versión 2 mediante publicación
  `6100cd4b-acc9-40ab-8f0f-6bb2c577bf4c`. Se conservaron vigente, revisión, las dos versiones,
  los dos artefactos, actividad y eventos. Repetir no duplicó relaciones; no quedan propuestas
  publicadas sin publicación de destino en esa base
  (`/private/tmp/registry-publication-links-recovery.log`).
- Comprobación en Chrome autenticado: ambas fichas muestran su publicación, `dgonzale9`,
  la fecha original, **Active**, las versiones 1 y 2 respectivamente y el enlace a su
  propuesta. Se abrió el enlace de la segunda y mostró **Published** y sus diferencias;
  se dejó abierta la ficha original reportada. No se ejecutaron acciones de publicación
  ni retirada desde el navegador.

La reconciliación antigua exige procedencia inequívoca de artefactos y del actor original;
los casos sin esa evidencia requieren revisión, sin adivinar desde la vigente ni reactivar
destinos retirados. La guía incluye orden de despliegue y restricciones de rollback.
Esta integración conserva los cambios ajenos y se somete a su propio gate final independiente.

### Corrección B2 del gate de publicaciones

El primer gate devolvió `DO NOT CLOSE`: una propuesta histórica sin FK podía reutilizar
un intento activo posterior cuando el mismo intake/destino tenía un intento retirado.
Ahora la reconciliación comprueba ese historial antes de buscar un intento activo y
devuelve `publication_evidence_missing`. Las publicaciones nuevas y los replays que ya
tienen FK conservan su comportamiento.

La nueva regresión reprodujo el fallo antes de corregirlo
(`/private/tmp/registry-publication-links-b2-red.log`). Con la corrección, **42 pruebas
aprobadas** de publicaciones, estado de intakes y retirada de destinos, incluidas las
14 regresiones de esta integración. La prueba B2 compara la totalidad de publicaciones,
asignaciones, propuestas, datasets y versiones antes y después del rechazo; no cambian
FK, estados, actores, fechas ni versiones (`/private/tmp/registry-publication-links-b2-tests.log`).
Mypy sin incidencias en los dos archivos del ajuste B2
(`/private/tmp/registry-publication-links-b2-types.log`); se conservan los diagnósticos
preexistentes documentados para Admin/intakes. No se modificaron los registros reales
reparados, que no tienen ese historial de retiradas. El gate se solicita de nuevo tras
las comprobaciones correspondientes.

## Filtros de fechas en el catálogo de Django Admin (2026-10-29)

Alcance: únicamente el catálogo Dataset del Admin. Se añade **Date range**, con selector
**Last successful update** / **Created date**, **From**, **To**, **Apply dates** y
**Clear dates**, sin JavaScript adicional, migraciones, mutaciones ni cambios de API/FE.
La fecha de actualización mantiene el significado existente de `current_version.created_at`;
su presentación ahora utiliza la misma zona horaria local que el filtro.

- TDD: 14 regresiones fallaron inicialmente porque el Admin no reconocía los parámetros
  (`/private/tmp/registry-date-filter-red.log`). La primera implementación dejó 12 aprobadas;
  se corrigió una fixture que otorgaba permisos de organización al supuesto lector y una
  consulta por fila al representar `Workspace.__str__`. El Admin carga ahora también
  `workspace__organization`, sin alterar las ACL compartidas.
- **30 pruebas aprobadas**: `test_dataset_admin_date_filters.py`,
  `test_dataset_management_admin.py` y `test_dataset_management.py`, en PostgreSQL aislado
  y con almacenamiento externo desactivado (`/private/tmp/registry-date-filter-tests.log`).
  Cubren límites inclusivos del día con cambio de horario, extremos opcionales, fechas
  inválidas, selección de la vigente frente al historial, datasets sin versión, permisos
  de workspace/host, búsqueda/orden/paginación y consultas constantes al pasar de 1 a 9 filas.
- Ruff formato/lint, Black, mypy estricto de los tres archivos Python modificados,
  `git diff --check` y Django system check correctos
  (`/private/tmp/registry-date-filter-quality.log`). No se añadieron dependencias.
- Comprobación visual en Chrome autenticado sobre la URL reportada (`?o=6`): controles
  visibles en inglés, fechas y zona **America/New_York** legibles. Aplicar 29/10/2026 en
  ambos extremos pasó de 38 datasets a 2; **Clear dates** restituyó los 38 y limpió los
  campos. La tabla muestra la hora local consistente con el rango. Se dejó abierto el
  catálogo sin rango; no se modificaron registros reales desde el navegador.

Los fallos anteriores de intakes documentados arriba pertenecen a otra entrega y no se
ejecutaron en este alcance. El gate final de esta corrección queda limitado a los filtros,
su integración con el catálogo autorizado y las restricciones permanentes del repositorio.
