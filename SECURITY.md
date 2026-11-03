# Security

Please report suspected vulnerabilities through GitHub's private vulnerability
reporting: open this repository's **Security → Report a vulnerability** page.
Include the affected version, reproduction steps and likely impact. Do not
open a public issue for a vulnerability or include real keys or personal data.

The supported desktop platform is Windows 10/11 x64. The installed app's backend
listens only on loopback and uses a local API token for HTTP and WebSocket
access. Installed builds store API keys in Windows Credential Manager. These
local boundaries and protection of stored data and credentials are in scope.

This is a personal, non-commercial fan project. There is no bug bounty.
