# Containerizing an Application with Docker

A step-by-step guide to packaging an application and its dependencies into a Docker image and running it as a container.

## 1. Overview

Containerization packages an application together with its runtime, libraries, and configuration into a single portable unit called an **image**. A running instance of an image is called a **container**. Because the image carries everything the application needs, it behaves the same on a developer laptop, a CI server, and a production host.

This guide uses a Python (Flask) application as the worked example. The process is the same for any stack; only the base image, the dependency installation step, and the start command change. Variations for other stacks are given in Section 6.

### Key terms

| Term | Meaning |
| --- | --- |
| Image | A read-only template containing the application, its dependencies, and its filesystem. |
| Container | A running (or stopped) instance of an image. |
| Dockerfile | A text file of instructions that tells Docker how to build an image. |
| Layer | Each Dockerfile instruction creates a cached layer; unchanged layers are reused on later builds. |
| Registry | A server that stores and distributes images (for example Docker Hub, GitHub Container Registry, AWS ECR). |
| Build context | The directory sent to Docker when building; `COPY` can only access files inside it. |

## 2. Prerequisites

1. The application is set up and **verified to work locally**, including all of its dependencies.
2. Docker is installed on your machine (Docker Desktop on Windows and macOS, Docker Engine on Linux).
3. Confirm the installation and that the Docker daemon is running:

```bash
docker --version
docker info
```

If `docker info` reports that it cannot connect to the daemon, start Docker Desktop (or the `docker` service on Linux) before continuing.

## 3. Prepare the Application

Before writing a Dockerfile, make sure the project can be built reproducibly.

### 3.1 Declare all dependencies

Every package the application needs must be listed in a dependency file at the project root, because the container starts with nothing installed. For Python this is `requirements.txt`.

- **Pin versions** (for example `flask==3.0.3`) so builds are repeatable.
- Generate the file from a clean virtual environment with `pip freeze > requirements.txt`, then remove anything not needed at runtime (such as test or linting tools, which belong in a separate `requirements-dev.txt`).
- If you serve with a production server such as Gunicorn, include it in the file.

### 3.2 Make the app reachable from outside the container

A server bound to `127.0.0.1` (localhost) inside a container is **not reachable** from the host. The application must listen on all interfaces, `0.0.0.0`. This is the single most common reason a container appears to run but does not respond.

For Flask's built-in server:

```python
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

### 3.3 Externalize configuration

Do not hard-code secrets, database URLs, or environment-specific values. Read them from environment variables (for example `os.environ["DATABASE_URL"]`) so the same image can run in development, staging, and production. Never bake passwords or API keys into the image.

### 3.4 Typical project layout

```text
my-app/
├── app.py
├── requirements.txt
├── Dockerfile
├── .dockerignore
└── (templates/, static/, other source files)
```

## 4. Create a `.dockerignore` File

Create `.dockerignore` in the project root. It works like `.gitignore` and excludes files from the build context. This makes builds faster, keeps images smaller, and prevents secrets from being copied into the image by `COPY . .`.

```text
__pycache__/
*.pyc
.venv/
venv/
.git/
.gitignore
.env
*.log
.pytest_cache/
.idea/
.vscode/
Dockerfile
.dockerignore
```

Adjust the list for your stack (for Node.js, add `node_modules/`).

## 5. Write the Dockerfile

Create a file named exactly `Dockerfile` (no extension) in the root directory of the application. Use the template below as a starting point and change the values marked in the comments to match your application.

```dockerfile
# 1. Choose a base image that matches your language and version
FROM python:3.13-alpine

# 2. Set environment variables for predictable Python behaviour
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 3. Set the working directory inside the container
WORKDIR /app

# 4. Copy only the dependency file first to take advantage of layer caching
COPY requirements.txt .

# 5. Install dependencies without keeping pip's download cache
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy the rest of the application source code
COPY . .

# 7. Create and switch to a non-root user
RUN adduser -D appuser
USER appuser

# 8. Document the port the application listens on
EXPOSE 5000

# 9. Define the command that starts the application
CMD ["python", "app.py"]
```

### 5.1 Instruction-by-instruction explanation

- **`FROM`** selects the base image. Always specify a version tag; avoid `latest`, because it changes over time and makes builds unpredictable. The base image must match your runtime (Python, Node, Java, and so on).
- **`ENV`** sets environment variables. `PYTHONDONTWRITEBYTECODE=1` stops Python writing `.pyc` files, and `PYTHONUNBUFFERED=1` sends log output straight to the container logs instead of buffering it.
- **`WORKDIR`** sets the directory in which later instructions and the running application operate. Docker creates it if it does not exist.
- **`COPY requirements.txt .`** followed by **`RUN pip install ...`** is deliberately placed *before* the source copy. Docker caches each layer, so as long as `requirements.txt` has not changed, dependency installation is skipped on rebuilds even when your code changes. This is the most effective build-speed optimization.
- **`--no-cache-dir`** tells pip not to store downloaded packages, which keeps the image smaller.
- **`COPY . .`** copies the remaining source into the image (respecting `.dockerignore`).
- **`USER`** drops root privileges. Running as a non-root user limits the damage if the application is compromised.
- **`EXPOSE`** is documentation only. It records which port the application uses but does **not** publish the port; publishing is done with `-p` at run time.
- **`CMD`** defines the default start command. Use the exec (JSON array) form shown above so the application receives signals such as `SIGTERM` and shuts down cleanly. `CMD` can be overridden when running the container; use `ENTRYPOINT` when the container should always run a fixed executable.

### 5.2 Choosing a base image

| Variant | Characteristics | Use when |
| --- | --- | --- |
| `python:3.13-alpine` | Very small, uses musl libc. | Pure-Python dependencies; smallest image matters. |
| `python:3.13-slim` | Small, Debian-based (glibc). | Dependencies with compiled extensions (for example `numpy`, `pandas`, `psycopg2`); best general default. |
| `python:3.13` | Full Debian image, large. | You need many system tools and size is not a concern. |

Alpine can cause slow builds or failures for packages that ship prebuilt wheels only for glibc. If installation fails on Alpine, switch to the `-slim` variant. Always confirm the base image tag exists on Docker Hub and matches the Python version your application was tested with.

### 5.3 System-level packages

If your application needs operating-system libraries (for example a database client library), install them in the same `RUN` step as their cleanup so the layer stays small.

Alpine:

```dockerfile
RUN apk add --no-cache build-base libffi-dev
```

Debian/Ubuntu-based images:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev \
    && rm -rf /var/lib/apt/lists/*
```

Note that the non-root user creation command differs by distribution: Alpine uses `adduser -D appuser`, while Debian-based images use `useradd --create-home appuser`.

## 6. Adapting the Template to Your Stack

Only three things normally change: the base image, how dependencies are installed, and the start command.

| Stack | Base image | Dependency step | Start command |
| --- | --- | --- | --- |
| Flask (development) | `python:3.13-slim` | `pip install -r requirements.txt` | `["python", "app.py"]` |
| Flask (production) | `python:3.13-slim` | same, with `gunicorn` in requirements | `["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]` |
| Django | `python:3.13-slim` | same, with `gunicorn` | `["gunicorn", "--bind", "0.0.0.0:8000", "project.wsgi:application"]` |
| FastAPI | `python:3.13-slim` | same, with `uvicorn` | `["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]` |
| Node.js | `node:22-alpine` | `COPY package*.json ./` then `RUN npm ci --omit=dev` | `["node", "server.js"]` |

In `gunicorn app:app`, the first `app` is the Python module (file) name and the second is the Flask application object inside it. Adjust both to match your project.

> **Important:** Flask's built-in server (`app.run`) is intended for development only. For production use a WSGI server such as Gunicorn or uWSGI.

## 7. Build the Image

Run the build command from the directory containing the Dockerfile. The trailing `.` is the build context and is required.

```bash
docker build --tag my-app-image .
```

The short form is equivalent:

```bash
docker build -t my-app-image .
```

### 7.1 Tagging and versioning

An image name has the form `name:tag`. If you omit the tag, Docker uses `latest`. Use explicit version tags for anything beyond local experiments:

```bash
docker build -t my-app-image:1.0.0 .
```

### 7.2 Useful build options

| Option | Purpose |
| --- | --- |
| `-f path/to/Dockerfile` | Use a Dockerfile with a different name or location. |
| `--no-cache` | Ignore cached layers and rebuild everything. |
| `--build-arg KEY=value` | Pass a build-time variable declared with `ARG`. |
| `--platform linux/amd64` | Build for a specific CPU architecture (important when building on Apple Silicon for an x86 server). |

Confirm the image exists:

```bash
docker images
```

## 8. Run a Container

Use the `-p` flag to publish the container's port on your host. The format is `HOST_PORT:CONTAINER_PORT`.

```bash
docker run -p 5000:5000 my-app-image
```

- The **container port** (right side) is the port your application listens on inside the container (the one in your code and `EXPOSE`).
- The **host port** (left side) is the port you use from your machine. They do not have to match; `-p 8080:5000` makes the app available at `http://localhost:8080` while it still listens on 5000 internally.
- The host port must be free. If it is already in use, choose another.

Open `http://localhost:<host-port>` in a browser or use `curl` to verify the application responds.

### 8.1 Common run options

```bash
docker run -d \
  --name my-app \
  -p 5000:5000 \
  -e FLASK_ENV=production \
  --env-file .env \
  -v app-data:/app/data \
  --restart unless-stopped \
  my-app-image
```

| Option | Purpose |
| --- | --- |
| `-d` | Run in the background (detached). |
| `--name` | Give the container a fixed name instead of a random one. |
| `-e KEY=value` | Set an environment variable. |
| `--env-file .env` | Load many environment variables from a file (keep this file out of the image and out of version control). |
| `-v name:/path` | Mount a volume so data persists after the container is removed. |
| `--restart` | Restart policy, for example `unless-stopped`. |
| `--rm` | Automatically remove the container when it stops (useful for testing). |
| `-it` | Interactive terminal, for example with `sh` to inspect the container. |

> **Data persistence:** A container's filesystem is temporary. Anything written inside it is lost when the container is removed. Use volumes for databases, uploads, and any data that must survive.

## 9. Verify and Manage Containers

```bash
docker ps                      # list running containers
docker ps -a                   # list all containers, including stopped
docker logs my-app             # view output
docker logs -f my-app          # follow logs live
docker exec -it my-app sh      # open a shell inside a running container
docker stop my-app             # stop gracefully
docker start my-app            # start a stopped container
docker rm my-app               # remove a stopped container
docker rmi my-app-image        # remove an image
docker system prune            # remove unused containers, networks, and dangling images
```

If a container exits immediately, run `docker logs <container>` first; the cause is almost always a missing dependency, a wrong start command, or an unhandled error at startup. Use `/bin/bash` instead of `sh` on Debian-based images if available.

## 10. Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| Browser cannot connect although the container is running | App is bound to `127.0.0.1`; bind to `0.0.0.0`. Also check the `-p` mapping. |
| `port is already allocated` | The host port is in use. Stop the other process or use a different host port. |
| `ModuleNotFoundError` at startup | A package is missing from `requirements.txt`, or the file was not copied. |
| Build is slow on every change | `COPY . .` appears before dependency installation; reorder as shown in Section 5. |
| Large image size | Use a `-slim` or Alpine base, add `.dockerignore`, use `--no-cache-dir`, consider a multi-stage build. |
| `COPY failed: file not found` | The file is outside the build context or excluded by `.dockerignore`. |
| Pip fails compiling a package on Alpine | Install build tools, or switch to a `-slim` base image. |
| Permission denied writing files | The non-root user lacks write access; adjust ownership with `COPY --chown=appuser:appuser` or `chown`. |
| Exec format error | Image built for a different CPU architecture; rebuild with `--platform`. |

## 11. Best Practices

1. Pin the base image and dependency versions.
2. Order Dockerfile instructions from least to most frequently changing to maximize caching.
3. Always include a `.dockerignore`.
4. Run as a non-root user.
5. Keep one primary process per container.
6. Keep secrets out of the image; inject them at run time via environment variables or a secrets manager.
7. Use a production-grade server rather than a development server.
8. Log to standard output and standard error so `docker logs` works.
9. Keep images small and scan them for vulnerabilities (for example with `docker scout` or Trivy).
10. Add a health check so orchestrators can detect a failing application.

### 11.1 Health check example

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1
```

This avoids needing `curl` in the image. Adjust the URL to a lightweight endpoint such as `/health`.

## 12. Optional: Multi-Stage Builds

A multi-stage build compiles or installs dependencies in one stage and copies only the results into a clean final stage, producing a smaller and more secure image.

```dockerfile
# Stage 1: build dependencies
FROM python:3.13-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: final runtime image
FROM python:3.13-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .
RUN useradd --create-home appuser
USER appuser
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
```

## 13. Optional: Docker Compose

When the application depends on other services (a database, cache, or message queue), or when the `docker run` command becomes long, define everything in a `compose.yaml` file at the project root.

```yaml
services:
  web:
    build: .
    ports:
      - "5000:5000"
    env_file:
      - .env
    depends_on:
      - db
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: example
    volumes:
      - db-data:/var/lib/postgresql/data

volumes:
  db-data:
```

Start and stop the whole stack with:

```bash
docker compose up --build
docker compose down
```

Within Compose, services reach each other by service name (for example host `db`), not `localhost`.

## 14. Optional: Publish the Image to a Registry

```bash
docker login
docker tag my-app-image yourusername/my-app-image:1.0.0
docker push yourusername/my-app-image:1.0.0
```

Anyone with access can then run it with `docker run -p 5000:5000 yourusername/my-app-image:1.0.0`.

## 15. Quick Reference

```bash
# Build
docker build -t my-app-image:1.0.0 .

# Run (foreground)
docker run -p 5000:5000 my-app-image:1.0.0

# Run (background, named, auto-restart)
docker run -d --name my-app -p 5000:5000 --restart unless-stopped my-app-image:1.0.0

# Inspect
docker ps
docker logs -f my-app

# Stop and clean up
docker stop my-app && docker rm my-app
```

## 16. Final Checklist

- [ ] Application runs correctly outside Docker.
- [ ] `requirements.txt` (or equivalent) is complete and version-pinned.
- [ ] Application binds to `0.0.0.0` and reads configuration from environment variables.
- [ ] `.dockerignore` created.
- [ ] `Dockerfile` created at the project root with a pinned base image.
- [ ] Image builds without errors.
- [ ] Container starts and responds on the mapped port.
- [ ] No secrets are stored in the image.
- [ ] A production server is used, and a non-root user is configured.
