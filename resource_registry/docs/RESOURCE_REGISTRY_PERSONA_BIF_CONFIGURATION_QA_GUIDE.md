# QA Guide — Resource Registry Persona and BIF Configuration

## Purpose

Use this guide to verify that a Persona exposes Resource Registry only when its
Workspace and BIF configuration allow it. Use synthetic datasets in a non-production
environment.

For user membership and data boundaries, see the
[User permissions QA guide](RESOURCE_REGISTRY_USER_PERMISSIONS_QA_GUIDE.md).

This guide calls the persisted function an **action** when describing model behavior. Actions are
assigned as canonical BIFs, then published through one of four grouped tools. In particular,
`plan_query`, `query_dataset`, and query-job actions are called through
`resource_registry_query`; `explore_dataset` is called through `resource_registry_inspect`.

## Test setup

Prepare one active, Registry-enabled Organization with two active, Registry-enabled
Workspaces, **Workspace A** and **Workspace B**. Publish one workspace-scoped test
dataset to each Workspace. Create two standard Workspace A users with no staff,
power-user, or publisher grant.

Use the following Personas:

| Persona | Workspace configuration | BIF configuration |
| --- | --- | --- |
| No-Registry Persona | Workspace A | No Resource Registry BIFs |
| Read Persona | Workspace A | List, dataset details, schema, profile, and versions |
| Query Persona | Workspace A | Read actions plus `plan_query`, `query_dataset`, `explore_dataset`, and query-job actions |
| Multi-Workspace Persona | Workspace A and B | Read BIFs plus query BIFs |

## Test scenarios

### 1. Persona without Registry BIFs

1. Sign in as an authorized Workspace A user.
2. Open the No-Registry Persona.
3. Ask it to list, read, or analyze Registry data.

Expected result: the Persona does not use Resource Registry. Assigning a Workspace alone
must not enable Registry access.

### 2. Minimum read configuration

1. Open the Read Persona as an authorized Workspace A user.
2. Ask to list datasets, then request details, schema, profile, and versions for the
   Workspace A dataset.
3. Ask for an analysis or SQL-style query.

Expected result: the assigned read actions are available for Workspace A data. Analysis is
unavailable because the Persona does not include the query or analysis BIF.

### 3. Query and analysis configuration

1. Open the Query Persona as a standard user and request a bounded read-only query of the Workspace A dataset.
2. Repeat as the second standard user.
3. Remove the canonical `resource_registry_query_dataset` assignment from the Persona and repeat as the first user.

Expected result: both standard users can query when the BIF, Workspace, Dataset, and current
policies admit it. Removing the assignment removes `query_dataset` from the
`resource_registry_query` action enum and makes querying unavailable to both users. A separate
analysis/chart/export action remains outside the default `restricted_reader` capabilities.

### 4. No configured Persona Workspace

1. Remove all Workspaces from a Persona that has at least one Resource Registry BIF.
2. Ask an otherwise authorized user to list datasets.

Expected result: Resource Registry is denied. A Registry-capable Persona requires at least one
configured and user-authorized Workspace.

### 5. One configured Persona Workspace

1. Configure only Workspace A on a Registry-capable Persona.
2. Use an authorized Workspace A user and ask to list datasets without naming a Workspace.
3. Request the Workspace B dataset by its known ID.

Expected result: Workspace A is selected automatically with the default `observe` policy. In
`strict`, make A active in the interface first. Workspace B data is unavailable.

### 6. Multiple configured Persona Workspaces

1. Configure Workspace A and Workspace B on the Multi-Workspace Persona.
2. Give the test user access to both Workspaces.
3. With the default `RESOURCE_REGISTRY_SCOPE_POLICY=observe`, open a fresh conversation and
   request datasets without naming a Workspace. Then explicitly say “Use Workspace A” and list.
4. In another fresh conversation, say “Use Workspace B” and list.
5. With `strict`, select Workspace A in the interface and list without naming it in chat. Ask to
   use B while A remains active. Switch the interface to B and list again in a fresh conversation.

Expected result: in `observe`, the first request in step 3 returns no Registry data, then the
explicit chat choices expose the named Workspace's `WORKSPACE` Datasets and any authorized
same-Organization `ORGANIZATION` Datasets. In `strict`, the active Workspace is
used; the cross-Workspace chat request executes no Registry tool and asks for an interface switch.

### 7. Configuration changes

1. Add or remove a Workspace or BIF from a test Persona.
2. Start a new conversation using that Persona.
3. Repeat the relevant scenario above.

Expected result: the new configuration controls the new conversation. Record the Persona,
configuration change, conversation start time, and observed behavior.

## Pass criteria

The release passes when a Persona's assigned canonical Registry BIFs control which grouped-tool
actions can be used, its Workspace configuration controls where data can be used, and role
capability does not bypass a missing Persona assignment or Workspace assignment.

## Related documentation

- [Getting started](RESOURCE_REGISTRY_GETTING_STARTED.md)
- [User permissions QA guide](RESOURCE_REGISTRY_USER_PERMISSIONS_QA_GUIDE.md)
- [Dataset publication QA guide](RESOURCE_REGISTRY_DATASET_PUBLICATION_QA_GUIDE.md)
