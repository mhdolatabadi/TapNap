# TapNap production deployment

TapNap deploys automatically after a successful push to `master`. A deployment can also be started manually from **Actions → Deploy → Run workflow**, optionally for a specific Git ref or commit.

The workflow deploys the exact resolved commit, builds the Docker image on the server, starts the Compose stack, and verifies the application from inside the container.

## One-time server setup

1. Install Git, Docker Engine, and the Docker Compose plugin.
2. Create the external network expected by `docker-compose.yml`:

   ```bash
   docker network create proxynet
   ```

3. Clone the repository into an absolute path, for example:

   ```bash
   sudo mkdir -p /home/apps
   sudo chown "$USER":"$USER" /home/apps
   git clone git@github.com:mhdolatabadi/TapNap.git /home/apps/tapnap
   cd /home/apps/tapnap
   ```

4. Create `data/credentials.json` from `data/credentials.example.json` and keep it out of Git.
5. Create a root `.env` containing any required runtime values such as `NESHAN_API_KEY` and `BALE_BOT_TOKEN`.
6. Ensure the deploy user can run `docker compose` without an interactive password prompt.
7. Add the public half of a dedicated deployment key to the deploy user's `~/.ssh/authorized_keys`.

The deploy keeps `data/` and the root `.env` intact because they are untracked and mounted/read on the server.

## GitHub production environment

Create an Environment named `production` and add:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Server IP address or hostname |
| `DEPLOY_USER` | SSH user allowed to run Docker |
| `DEPLOY_PATH` | Absolute clone path, such as `/home/apps/tapnap` |
| `DEPLOY_SSH_KEY` | Private half of the dedicated deployment key |
| `DEPLOY_KNOWN_HOSTS` | Verified output of `ssh-keyscan -H <host>` |
| `DEPLOY_PORT` | Optional SSH port; defaults to `22` |

Verify the host fingerprint independently before saving `DEPLOY_KNOWN_HOSTS`.

## Rollback

Run the Deploy workflow manually and enter the known-good commit SHA in the `ref` input. The server checks out that exact commit and rebuilds the stack.
