---
title: DNS Resolution Failures on Windows Server
topic: networking
---

# DNS Resolution Failures on Windows Server

**Platform:** Windows Server 2016/2019/2022, Active Directory-integrated DNS
**Severity:** High. Apps can't reach databases or APIs by name, and domain logons and Group Policy can fail.

## Symptoms

- `Ping request could not find host <name>` or `DNS name does not exist`, while pinging the IP address works.
- Apps log errors like `No such host is known` or `The remote name could not be resolved`.
- `nslookup` times out (`DNS request timed out`) or returns `Non-existent domain` for names that should exist.
- Name resolution works on some servers but not others, or only for internal names (or only for external ones).
- Domain-joined servers log **Event ID 1014** (`Name resolution for the name <x> timed out`) from source **DNS Client Events**.

## Likely Causes

1. The server's network adapter points to the wrong DNS servers, for example a public resolver instead of the domain controllers.
2. The DNS Server service is stopped or unhealthy on the domain controller.
3. A stale or missing record (A, CNAME) in the zone, or an old entry still in the client's DNS cache.
4. Conditional forwarders or root hints are broken, so external names fail.
5. A firewall blocks **UDP/TCP 53** between the client and the DNS server.
6. The zone isn't replicating between domain controllers.
7. A local `hosts` file entry overrides DNS.

## Diagnosis Steps

### 1. Check which DNS servers the client uses

```powershell
Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object ServerAddresses
ipconfig /all
```

### 2. Test resolution and say which server to ask

```powershell
Resolve-DnsName app01.corp.example.com
Resolve-DnsName app01.corp.example.com -Server 10.0.0.10    # ask a specific DC
Resolve-DnsName www.microsoft.com -Server 10.0.0.10           # external name
nslookup app01.corp.example.com 10.0.0.10
```

- Works against the DC but not by default → the client's DNS settings are wrong.
- Internal names work but external ones don't → forwarders or root hints.
- Nothing resolves against the DC → the DNS service or the network path.

### 3. Check the cache and the hosts file

```powershell
Get-DnsClientCache | Where-Object Entry -like "*app01*"
Get-Content C:\Windows\System32\drivers\etc\hosts
```

### 4. Check port 53 connectivity

```powershell
Test-NetConnection 10.0.0.10 -Port 53
```

### 5. On the DNS server

```powershell
Get-Service DNS
Get-DnsServerForwarder
Get-DnsServerResourceRecord -ZoneName "corp.example.com" -Name "app01"
dcdiag /test:dns /v
repadmin /replsummary
```

## Resolution

1. **Wrong DNS servers on the client:** point the adapter at the domain controllers.
   ```powershell
   Set-DnsClientServerAddress -InterfaceAlias "Ethernet" -ServerAddresses 10.0.0.10,10.0.0.11
   ```
2. **Stale cache:** clear it on the client and on the server.
   ```powershell
   Clear-DnsClientCache
   Clear-DnsServerCache          # on the DNS server
   ```
3. **Missing or wrong record:** fix it in DNS Manager, or with:
   ```powershell
   Add-DnsServerResourceRecordA -ZoneName "corp.example.com" -Name "app01" -IPv4Address 10.0.1.25
   ```
   Then run `ipconfig /registerdns` on the server that owns the name.
4. **DNS service stopped:** `Restart-Service DNS`, then look in the **DNS Server** event log for the reason.
5. **Broken forwarders:** fix them with `Set-DnsServerForwarder -IPAddress 1.1.1.1,8.8.8.8`, or use your upstream resolvers.
6. **Firewall:** allow UDP and TCP 53 from the client subnets to the DNS servers.
7. **Replication:** fix AD replication (`repadmin /syncall /AdeP`) before editing records on more than one DC.
8. Remove any incorrect `hosts` file entries.

## Prevention

- Set DNS servers through DHCP or Group Policy, not by hand on each server.
- Enable DNS **scavenging** with sensible aging intervals so stale records are cleaned up.
- Monitor DNS from outside: run a scheduled `Resolve-DnsName` for a few critical names against each DC and alert on failure.
- Run at least two DNS servers per site and list both on every client.
- Don't use `hosts` file entries on servers except as a short, documented workaround.

## Escalation

- Escalate to the Active Directory team for replication failures or zone corruption.
- Escalate to the network team if port 53 is blocked between subnets or sites.
