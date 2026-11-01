# Dev container

A Linux development environment for this repo in VS Code. Inside it, `uv run pytest` runs the
full suite, including the scikit-learn tests that Smart App Control can skip on Windows. CI builds
the same container and runs the tests in it (`ci.yml`, job `devcontainer`).

| Tool | Version | Source |
|---|---|---|
| Python | 3.12 (same minor as `.python-version` and CI) | `Dockerfile`: `FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm`, minus its Yarn apt source (missing signing key breaks `apt-get update` in the features) |
| uv | 0.13.1 (same as the extractor `Dockerfile`) | `post-create.sh` (`pipx install uv==…`) |
| Databricks CLI | 1.23.0 (same as CI) | local feature `features/databricks-cli` (official install script) |
| GitHub CLI | feature default (latest release at build time) | feature `github-cli:1` |
| Docker CLI | feature default (latest release at build time), using the host daemon | feature `docker-outside-of-docker:1` |

After the container is created, `post-create.sh` runs `uv sync --locked` (venv in
`/home/vscode/.venvs/energy-lakehouse-de`, outside the workspace). It does not install git hooks
(see [Git hooks](#git-hooks)).
You work as the non-root user `vscode`. No credentials are copied or mounted into the container.

## Open it

Prerequisites: Docker Desktop running, VS Code with the *Dev Containers* extension.

1. Open the repo folder in VS Code.
2. *Ctrl+Shift+P* → **Dev Containers: Reopen in Container**. The first build takes a few
   minutes; later starts reuse the image.
3. Check in the VS Code terminal (it runs inside the container):

   ```bash
   uv run pytest -q
   databricks -v && gh --version && docker --version
   ```

After a change to `.devcontainer/`, run **Dev Containers: Rebuild Container**.

## Log in to Databricks

Never copy `~/.databrickscfg` or tokens from the host. Log in inside the container with OAuth
(U2M). The host comes from `databricks.yml` (`workspace.host`):

```bash
databricks auth login --host https://dbc-405898bc-c90e.cloud.databricks.com --profile free
databricks current-user me -p free
```

The CLI prints a URL and waits on `localhost:8020`. VS Code forwards that port (see
`forwardPorts`), so the browser on the host completes the login. The token cache lives in the
container (`~/.databricks/`) and is gone after a rebuild; then log in again. All repo commands
work unchanged with `-p free`, for example `databricks bundle validate -t dev -p free`.

## Log in to GitHub

VS Code forwards your host git credentials, so `git push` / `git pull` work without a login.
The `gh` CLI needs its own login, which is also stored only in the container:

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth status
```

## Git hooks

Run `uv run pre-commit install` on the machine you commit from, once per clone. The hook
(`.git/hooks/pre-commit`) points at that machine's Python, and with a bind mount `.git/hooks` is
shared between the host and the container, so there is only one hook. If you commit on Windows,
install it on Windows; if you commit only inside the container (or use a container volume),
install it there. `post-create.sh` does not install it, so creating or rebuilding the container
never overwrites the host's hook.

## Known limitations (Windows)

- **Slow file access on a bind mount.** `C:\` is mounted through Docker Desktop, so `uv sync`
  and `pytest` are noticeably slower than on WSL or Linux. For speed, use **Dev Containers: Clone
  Repository in Container Volume** (the repo then lives only in a Docker volume).
- **Shared git hooks.** With a bind mount, `.git/hooks` belongs to both sides. A hook installed
  on Windows does not run in the container (CRLF, Windows path), and one installed in the
  container fails on Windows ("`pre-commit` not found"). Install it only where you commit (see
  [Git hooks](#git-hooks)); if a commit fails this way, rerun `uv run pre-commit install` on that
  side. With a container volume this does not occur.
- **Git "dubious ownership".** Files from `C:\` show up as owned by root, so `post-create.sh`
  adds the workspace to `safe.directory` in the container's own `~/.gitconfig`.
- **Line endings.** Shell scripts must be LF. `.gitattributes` forces `*.sh` to LF even with
  `core.autocrlf=true`. If a script fails with `$'\r'`, run `git add --renormalize .` and check
  out again.
- **`docker run` output.** Docker Desktop's socket is root-only, so the docker-outside-of-docker
  feature proxies it for `vscode` with `socat`. That proxy drops the output of `docker run`
  without a TTY after about half a second. Use `docker run -it ...`, or `docker run -d ...` and
  `docker logs`. `docker build` is not affected.
- **`-v` paths refer to the host.** The Docker daemon runs on the host, so
  `docker run -v "$PWD/...:/data"` mounts a host path that does not exist (`/workspaces/...`).
  To extract inside the container, run the extractor directly (`uv run python -m
  energy_lakehouse.extract_local --out data/landing`), or use a named volume and `docker cp`.
- **Logins don't survive a rebuild** (Databricks and `gh`). This is intentional: nothing secret
  is mounted from the host.
