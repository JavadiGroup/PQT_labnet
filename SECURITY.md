# Security policy

LabNet controls physical instruments. A flaw that lets someone use an
instrument without a lease, outside its limits, or without a person's
approval can damage equipment or samples, or hurt people. We treat those as
security vulnerabilities, along with the usual kinds: authentication, key
handling, TLS, injection, and escaping the Automation runner's containers.

## Supported versions

| Version | Supported |
| --- | --- |
| 3.0.x | Yes |

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately through GitHub:
on the repository's **Security** tab, choose **Report a vulnerability**.
Include:

- the LabNet version, and which roles are involved (Central, Lab PC,
  client, Control Center, watchdog, runner);
- what an attacker needs (a researcher's key? a computer on the lab network?
  a Control Center login? a prompt the agent reads?);
- the steps to reproduce, and what happens;
- logs, with API keys, proxy secrets and passwords removed.

We will acknowledge the report, keep you updated while we work on a fix,
and credit you in the release notes unless you'd rather we didn't. Please
give us a reasonable time to release a fix before you disclose it.

## Deployment advice

The [deployment guide](docs/deploying/index.md) and the
[Automation security model](docs/automation/security.md) describe how
LabNet expects to be run: TLS everywhere off loopback, one key per user and
per computer, Caddy logins in front of the Control Center, and site limits on
every instrument that may be automated. A report showing that those defences
fail is very welcome; one that requires turning them off is usually not a
vulnerability.
