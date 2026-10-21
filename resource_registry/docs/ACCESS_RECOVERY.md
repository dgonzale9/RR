# Resource Registry chat access recovery

Use this guide when chat cannot establish Resource Registry access before invoking a tool.
These responses replace the ambiguous “trusted tenant context is invalid” message. They describe
only the condition the application can establish; successful recovery after signing in again does
not prove which part of the previous session was stale.

## Scenarios and messages

| Diagnostic `error_code` | Confirmed condition | User-facing message |
| --- | --- | --- |
| `resource_registry_authentication_required` | The request lacks an authenticated, persisted user identity. | Your sign-in could not be verified for Resource Registry. Sign out, then sign in again on your organization's site. If the problem continues, clear cookies for this site and sign in again. |
| `invalid_resource_registry_tenant_context` | A dedicated legacy value in the request, session, or conversation is malformed. | I could not verify the Resource Registry context for this conversation. Sign out, then sign in again on your organization's site and start a new conversation. If the problem continues, clear cookies for this site and sign in again, or contact your administrator. |
| `resource_registry_persona_workspace_not_configured` | The Registry-capable Persona has no assigned Workspaces. | This Persona has no Workspace configured for Resource Registry. Ask an administrator to assign an enabled Workspace in this organization. |
| `resource_registry_persona_workspace_unavailable` | The active Workspace is unsupported by the Persona under `strict`, or no assigned Workspace is authorized and provisioned. | Select an authorized Workspace supported by this Persona in the interface, then retry. |
| `resource_registry_workspace_change_required` | Under `strict`, chat names a different Workspace from the active one, or the active selection cannot be admitted. No Registry operation executes. | Select the requested authorized Workspace in the interface, then retry. No operation was executed. |
| `resource_registry_persona_context_misconfigured` | Legacy Resource Registry metadata on the Persona is malformed. | This Persona's Resource Registry configuration needs attention. Ask an administrator to check its Workspace assignment and legacy Resource Registry settings. |
| `resource_registry_organization_disabled` | Resource Registry is explicitly disabled on the resolved organization. | Resource Registry is disabled for this organization. Ask an administrator whether it should be enabled. |
| `resource_registry_organization_unavailable` | The trusted organization or usable namespace context cannot be resolved. | I could not identify your organization for Resource Registry. Open the chat from your organization's site and sign in again. If the problem continues, ask an administrator to check the site's organization configuration. |
| `resource_registry_namespace_unavailable` | No eligible namespace is available for this request. | Resource Registry is not available to your account in this organization. Ask an administrator to check your access and the organization's Workspace setup. |

With the default `RESOURCE_REGISTRY_SCOPE_POLICY=observe`, the multi-Workspace selection flow asks
the user to name an authorized option in chat. Under `strict`, the user selects it in the interface;
chat cannot switch the active Workspace. These cases must not recommend signing out or silently
switch the selection. An empty dataset list is distinct from an access failure.

After a Workspace is admitted, the Registry BIF response distinguishes other conditions:

| BIF result | Meaning and recovery |
| --- | --- |
| `workspace_selection_required` | Select an authorized Workspace configured on the Persona. |
| `empty_reason: no_visible_datasets` | No Dataset is visible in the selected scope; check active assignments, Workspace/Organization visibility, and effective read policy. |
| `empty_reason: no_matching_datasets` | Search, filters, or pagination offset yielded no results; clear them before changing permissions. |
| `capability_error` | The BIF capability is denied by the effective policy; inspect tenant/Dataset policy and Persona tool assignment. |
| `invalid_column` | A query references a column outside the user's visible schema, including in a function or filter. |

No Dataset named literally “Content Shelf” is required for discovery. The current
`restricted_reader` default permits bounded read-only queries and the user's own query jobs;
persisted policy copies from older defaults may still deny them. Audit exact copies with
`upgrade_registry_reader_policies` as described in [reader access](../READER_ACCESS.md).

## Recovery order

1. Confirm the browser's organization site, intended account, and active Persona.
2. For unverified sign-in or conversation context, sign out and sign in again on that site.
   Start a new conversation when requested. Save any needed work before ending the session.
3. If renewal does not resolve it, clear cookies/site data only for the affected site and sign in
   again. Do not recommend clearing all browser history or all sites' cookies.
4. For persistent failures, an administrator checks the host mapping, Persona Workspace assignment,
   organization/Workspace enablement, namespace provisioning, and the user's current authorization.
   A known configuration failure should go directly to this step.

The application does not delete cookies, flush unrelated conversations, grant access, change
organizations, or substitute a different tenant as recovery. Existing cleanup when an organization
is disabled remains in effect. Denied requests execute no Registry tools. Direct BIF admission
continues to enforce its existing generic authorization boundary.

## Safe diagnosis

Each classified failure logs `Resource Registry access blocked` with its error code and numeric
actor, organization, and Persona IDs when available. These IDs stay in server diagnostics; they are
not included in the recovery response. Missing or non-integer IDs are logged as `None`.
The same fields are attached as `registry_error_code`, `registry_actor_id`,
`registry_organization_id`, and `registry_persona_id` for compatible structured log formatters.

Support can collect the environment, timestamp/timezone, Request URL without query parameters,
Persona name/ID, displayed message, and diagnostic error code. Never request or record cookies,
session identifiers, authorization headers, SAS URLs, raw legacy context values, or chat contents
for this diagnosis. Do not paste the browser's complete Request Headers into a ticket.

The chat endpoint can return HTTP 200 with one of these application-level failure messages.
Inspect the response and diagnostic code; the status alone does not establish Registry access.
Existing `no_authorized_namespace` and `organization_context_unavailable` routing statuses are
preserved. The legacy `invalid_resource_registry_tenant_context` code now specifically identifies
malformed request/session/conversation context; other causes have distinct codes.

The wording follows [GOV.UK's plain-language error guidance](https://design-system.service.gov.uk/components/error-message/).
Diagnostic redaction follows [OWASP's session logging guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#logging-sessions-life-cycle-monitoring-creation-usage-and-destruction-of-session-ids).

See [User permissions](../docs/RESOURCE_REGISTRY_USER_PERMISSIONS.md) and
[Persona Workspace scope](../docs/RESOURCE_REGISTRY_PERSONA_WORKSPACE_SCOPE.md) for admission rules.
