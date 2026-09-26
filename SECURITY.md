# Security policy

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Omniaeye/omnia-mcp/security/advisories/new) when it is enabled. Include the affected version, minimal reproduction, impact, and proposed mitigation if known. Do not post credentials or sensitive decision inputs in public issues.

If private reporting is unavailable, open an issue asking for a private channel without disclosing exploit details. There is no promised response-time SLA.

## Supported releases

Security fixes target the latest released version. Check release notes before upgrading. Earlier versions and unreleased source may require migration.

## Trust boundaries

- A local MCP server runs with its operating-system user's permissions. Review its source and launch configuration.
- JEV sends evaluation inputs to TypeSafe. Use credentials with the minimum available permissions and review provider data terms.
- Local LAYA needs model assets. Use approved identifiers and full revisions; prepare assets before offline operation.
- SQLite and its backups may contain labels, evidence references, rubric descriptions, results, and identifiers. They are not anonymized. Restrict access and review automatic retention.
- Credentials must not appear in tool inputs, returned errors, screenshots, fixtures, or logs.
- Tool annotations guide clients; they are not authorization controls. Server-side validation and policy remain necessary.

## Deployment scope

This release exposes stdio and assumes one local operator context. A public HTTP bridge does not make it a multi-tenant service. Remote hosting requires authentication, authorization, tenant isolation, rate limits, and data-lifecycle design.

Use a dedicated account or container when appropriate. The Docker image runs without root and expects persistent storage. Keep dependencies, the base image, and the MCP client current.

## Decision scope

The server returns typed assessments. It does not sign transactions, hold wallet keys, execute trades, or authorize external actions. Do not grant those privileges to the process simply because it can evaluate financial or operational context.
