---
title: Expired or Invalid SSL Certificate on IIS Site
topic: security
---

# Expired or Invalid SSL Certificate on IIS Site

**Platform:** Windows Server / IIS 10
**Severity:** High. Browsers block the site with a security warning, and API clients fail TLS handshakes.

## Symptoms

- Browsers show `NET::ERR_CERT_DATE_INVALID`, `ERR_CERT_COMMON_NAME_INVALID` or "Your connection is not private".
- API clients fail with `The remote certificate is invalid according to the validation procedure` or `SSL certificate problem: certificate has expired`.
- The HTTPS site doesn't respond at all, or returns `ERR_CONNECTION_RESET`, after a certificate was renewed or replaced.
- System Event Log entries from source **Schannel**, for example **Event ID 36874** or **36888** (TLS handshake errors).

## Likely Causes

1. The certificate has expired.
2. The certificate was renewed, but the IIS HTTPS **binding** still points to the old certificate's thumbprint.
3. The hostname doesn't match the certificate's Subject Alternative Names (SAN), for example `www.` is missing.
4. The intermediate certificate (chain) is missing from the server, so clients can't build a trust path.
5. The certificate was imported without its private key, or the app pool identity can't read the private key.
6. With several sites on one IP, **SNI** (Server Name Indication) isn't enabled, so the wrong certificate is served.

## Diagnosis Steps

### 1. Check what the site actually serves

From any machine:

```powershell
$req = [Net.HttpWebRequest]::Create("https://www.example.com")
$req.GetResponse() | Out-Null
$cert = $req.ServicePoint.Certificate
"Subject: $($cert.Subject)  Expires: $($cert.GetExpirationDateString())"
```

```bash
openssl s_client -connect www.example.com:443 -servername www.example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
```

### 2. List the server's certificates and their expiry dates

```powershell
Get-ChildItem Cert:\LocalMachine\My |
  Select-Object Subject, Thumbprint, NotAfter, HasPrivateKey |
  Sort-Object NotAfter
```

### 3. Check the IIS bindings

```powershell
Import-Module WebAdministration
Get-WebBinding -Protocol https | Select-Object bindingInformation, certificateHash, sslFlags
```

```cmd
netsh http show sslcert
```

Compare the thumbprint (`certificateHash`) with the current certificate from step 2. `sslFlags = 1` means SNI is enabled.

### 4. Check the chain

```powershell
certutil -verify -urlfetch C:\temp\site.cer
```

Or use an online SSL checker and look for "chain incomplete" or "extra download".

### 5. Check private key access

In `certlm.msc`, right-click the certificate and choose **All Tasks → Manage Private Keys**. The app pool identity, or `IIS_IUSRS`, needs **Read** access.

## Resolution

1. **Expired:** renew the certificate with your CA (or win-acme/Certbot for Let's Encrypt) and import the PFX, including the private key:
   ```powershell
   $pwd = Read-Host -AsSecureString
   Import-PfxCertificate -FilePath C:\certs\site.pfx -CertStoreLocation Cert:\LocalMachine\My -Password $pwd
   ```
2. **Binding points to the old certificate:** update the binding to the new thumbprint.
   ```powershell
   $binding = Get-WebBinding -Name "MySite" -Protocol https -HostHeader "www.example.com"
   $binding.AddSslCertificate("<NEW_THUMBPRINT>", "My")
   ```
3. **Name mismatch:** reissue the certificate with every hostname in the SAN list.
4. **Missing chain:** import the CA's intermediate certificate into `Cert:\LocalMachine\CA`.
5. **Private key:** re-import the PFX with its key, or grant the app pool identity Read access to the key.
6. **Wrong certificate on a shared IP:** turn on **Require Server Name Indication** for each HTTPS binding that has a host name.
7. Restart the site and check again with step 1 of Diagnosis.

## Prevention

- Automate renewal (win-acme, Certbot, or your CA's ACME endpoint) and make the binding update part of the same script.
- Alert 30 days and 7 days before expiry, for example with a scheduled script that checks `NotAfter` or an uptime monitor with certificate checks.
- Keep an inventory of every certificate, where it is bound, and who owns it.
- Prefer certificates with every hostname in the SAN list over several single-name certificates.

## Escalation

- Escalate to the PKI or security team for internal CA problems, revoked certificates or certificate policy questions.
- Escalate to the domain or DNS owner if the CA's domain validation fails.
