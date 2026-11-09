# SentinelOps

**Infrastructure Automation | Docker | CI/CD | Monitoring**

SentinelOps is a DevOps portfolio project focused on building, deploying and managing a web application using modern infrastructure and automation practices.

## Project overview

The goal of SentinelOps is to create a reliable, automated and maintainable application environment.

The project covers infrastructure setup, application deployment, automation, monitoring and troubleshooting.

## Project objectives

- Build and deploy a lightweight web application.
- Configure and manage a Linux environment.
- Implement version control with Git and GitHub.
- Containerize the application using Docker.
- Automate configuration management with Ansible.
- Build a CI/CD pipeline for testing and deployment.
- Implement health checks and logging.
- Apply basic security best practices.
- Document architecture and troubleshooting procedures.

## Technology Stack

| Category | Technology |
|----------|------------|
| Operating System | Ubuntu Linux |
| Programming | Python |
| Version control | Git & GitHub |
| Containerization | Docker |
| Automation | Ansible |
| Monitoring | Custom Python uptime monitor (`monitor.py`) |

## Architecture

Architecture diagrams and infrastructure details will be added as the project develops.

## The web app

Type a company name or website, and SentinelOps checks it and explains the result in plain language.

- **Search:** works with a company name (`Spotify`), a domain (`spotify.com`) or a full URL. Typos get a suggestion.
- **Status:** every watched service is rechecked each minute, with response history and uptime.
- **Diagnosis:** each part of a service is tested in five steps: finding the address (DNS), reaching the server (TCP), securing the connection (TLS), loading the page (HTTP) and measuring the speed. The first step that fails names the real problem.
- **Routing:** each problem becomes a ticket, sent to the department that handles that kind of fault and assigned to the agent with the fewest open tickets. Tickets close by themselves when a check passes again.
- **Demo outages:** real sites are rarely down, so any service can run a pretend outage to show routing in action. These are labelled as simulated and don't affect uptime.

Website checks are real. The departments, agents and tickets are fictional, and nothing is sent to anyone.

| Problem | Department |
|---------|------------|
| Website address can't be found, server won't connect | Network Operations |
| Invalid or expiring certificate, unsafe redirect | Security Operations |
| Server errors, no response in time | Platform Engineering |
| Missing pages, redirect loops | Web Applications |
| Slow responses | Performance & Reliability |

## Project structure

| Path | Purpose |
|------|---------|
| `dashboard.py` | Web server and JSON API |
| `engine.py` | Runs checks, keeps history, opens and closes tickets |
| `diagnostics.py` | The five-step health check |
| `catalog.py` | Turns a search into something to check |
| `routing.py` | Departments, agents and routing rules |
| `store.py` | SQLite storage |
| `static/` | Frontend, plain HTML, CSS and JavaScript |
| `data/companies.json` | Directory of known companies |
| `monitor.py` | Command-line uptime monitor |
| `targets.txt` | URLs for the command-line monitor |
| `test_*.py` | Unit tests, no internet needed |

## Getting started

Requires Python 3 and `requests`. Tested on Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the web app. It opens at http://127.0.0.1:8000, and Ctrl+C stops it:

```bash
python3 dashboard.py
```

The app only listens on this computer. Run history is saved in `data/sentinelops.db`.

### Command-line monitor

Check every URL in `targets.txt`, or pass URLs directly:

```bash
python3 monitor.py
python3 monitor.py https://example.com github.com
python3 monitor.py -f my-targets.txt --timeout 3 --workers 2
```

```text
STATE CODE       TIME  TARGET                          DETAIL
UP    200      347 ms  https://example.com
DOWN  503      789 ms  https://httpbin.org/status/503  Service Unavailable
DOWN  -             -  http://127.0.0.1:9              connection refused
DOWN  -             -  https://no-such-host.invalid    DNS lookup failed

1/4 up, 3 down
```

A target counts as up when it answers with a status below 400, after following redirects.

| Exit code | Meaning |
|-----------|---------|
| 0 | All targets are up |
| 1 | At least one target is down |
| 2 | Bad usage, such as no targets or an unreadable file |

### Tests

```bash
python3 -m unittest
```

## Project status

**Status: In Development**

The Linux environment is set up and the monitor and web app are working. Docker, Ansible and the CI/CD pipeline are next.

## Documentation

Technical documentation will include:

- Infrastructure configuration
- Deployment instructions
- Automation workflows
- Monitoring and health checks
- Security considerations
- Troubleshooting and lessons learned
