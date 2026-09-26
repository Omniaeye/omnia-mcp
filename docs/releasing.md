# Releasing

The primary distribution for 0.1.0 is the versioned GitHub source tag. `server.json` contains identity metadata until a real package or remote endpoint is published. It is not an assertion of MCP Registry availability.

## Source release

1. Run validation and build wheel/sdist artifacts from the intended commit.
2. Install the wheel in a clean environment outside the checkout. Check version, diagnostics, schemas, and stdio discovery.
3. Confirm package metadata, CLI, manifest, docs, and changelog agree on the version.
4. Inspect artifacts for credentials, local databases, caches, model files, and unrelated source.
5. Create the reviewed tag and GitHub release. Attach artifacts, checksums, and actual validation evidence.
6. Verify the documented tagged-source installation from a clean environment.

Keep release tags immutable; publish fixes as new versions. A wheel can be installed with pip without Git or a checkout.

## Optional PyPI publication

`publish-pypi.yml` is manual and accepts an existing release tag. It rebuilds and checks the distribution, then publishes through PyPI Trusted Publishing in the `pypi` GitHub environment. Pushes and tags do not trigger it.

Before enabling publication, configure the PyPI project, repository/workflow identity, and `pypi` environment with required reviewers. Confirm the package name is available and publication is intended. No persistent PyPI token belongs in the repository.

After a package actually exists and installation is verified, update the README and `server.json` with its real registry identifier/version. Keep the README's `mcp-name` marker consistent.

## MCP Registry

Registry publication is separate. Package entries must reference an existing artifact; a source repository is not a PyPI or OCI package. Remote entries require an implemented, publicly reachable MCP endpoint.

Do not add speculative `packages` or `remotes`. The current source-only metadata is intentionally not a registry-ready installation manifest. Registry listing, client-catalog approval, PyPI publication, and GitHub release are separate states.

## Dependencies

A repository lockfile governs environments installed with the corresponding tooling. Consumers installing a package do not automatically apply the repository's `uv.lock`.

Review dependency updates through CI. Verify action pins against upstream and update container bases. Docker tags are mutable; reproducible deployments should select and record a verified image digest.

References: [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/), [MCP package types](https://modelcontextprotocol.io/registry/package-types), [Registry publishing](https://modelcontextprotocol.io/registry/github-actions).
