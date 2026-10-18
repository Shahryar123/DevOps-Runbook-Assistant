---
title: IIS Application Pool Crashes
topic: deployment
---

# IIS Application Pool Crashes

**Platform:** Windows Server / IIS 10
**Severity:** High — the site returns HTTP 503 Service Unavailable while the pool is stopped.

## Symptoms

- Users get `HTTP Error 503. The service is unavailable.`
- The app pool shows **Stopped** in IIS Manager, even though nobody stopped it.
- System Event Log entries from source **WAS**:
  - **Event ID 5002**: "Application pool 'X' is being automatically disabled due to a series of failures in the process(es) serving that application pool." (rapid-fail protection was triggered)
  - **Event ID 5011**: "A process serving application pool 'X' suffered a fatal communication error with the Windows Process Activation Service."
  - **Event ID 5009**: "A process serving application pool 'X' terminated unexpectedly."
- Application Event Log entries with **Event ID 1000** (Application Error) for `w3wp.exe`, or **1026** (.NET Runtime).

## Likely Causes

1. Unhandled exception in application code, for example a StackOverflowException or an exception on a background thread.
2. The app pool identity lacks permissions on the site folder, the config files or a dependent resource.
3. The app pool uses the wrong .NET CLR version or the wrong 32-bit/64-bit setting for the application.
4. A missing or broken dependency, such as a DLL, the ASP.NET Core Hosting Bundle or the URL Rewrite module.
5. A malformed `web.config` (look for HTTP 500.19 in the IIS logs).
6. Memory exhaustion or a private-bytes recycling limit set too low.
7. The service account's password was changed, so the custom identity can no longer log on.

## Diagnosis Steps

### 1. Confirm the pool state

```powershell
Import-Module WebAdministration
Get-WebAppPoolState -Name "MyAppPool"
Get-ChildItem IIS:\AppPools | Select-Object Name, State, managedRuntimeVersion, enable32BitAppOnWin64
```

```cmd
%windir%\system32\inetsrv\appcmd list apppool /name:"MyAppPool" /text:*
```

### 2. Read the event logs

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WAS'; StartTime=(Get-Date).AddHours(-24)} |
  Select-Object TimeCreated, Id, Message | Format-List

Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000,1026; StartTime=(Get-Date).AddHours(-24)} |
  Where-Object Message -match 'w3wp' | Select-Object TimeCreated, Id, Message | Format-List
```

### 3. Check the rapid-fail protection settings

By default, 5 worker process failures within 5 minutes disable the pool.

```cmd
appcmd list apppool "MyAppPool" /text:failure.rapidFailProtection
appcmd list apppool "MyAppPool" /text:failure.rapidFailProtectionMaxCrashes
```

### 4. Verify the identity and its permissions

```powershell
(Get-ItemProperty IIS:\AppPools\MyAppPool).processModel | Select-Object identityType, userName
icacls "D:\inetpub\MyApp"
```

The identity (for example `IIS AppPool\MyAppPool`) needs at least Read & Execute on the site folder.

### 5. Capture a crash dump

If the event logs don't explain the crash, take a dump of `w3wp.exe` when it fails.

```cmd
procdump.exe -ma -e -w w3wp.exe C:\dumps
```

You can also use **DebugDiag** with a crash rule for the app pool. To analyse the dump, use the DebugDiag Analysis tool or WinDbg (`!analyze -v`, `!pe` for managed exceptions).

### 6. ASP.NET Core apps

Turn on stdout logging in `web.config` for a short while:

```xml
<aspNetCore processPath="dotnet" arguments=".\MyApp.dll" stdoutLogEnabled="true" stdoutLogFile=".\logs\stdout" />
```

Check that the Hosting Bundle matches the app's runtime: run `dotnet --list-runtimes`.

## Resolution

1. Fix the root cause you found above, for example by granting permissions, correcting the CLR or bitness setting, installing the missing dependency, or fixing `web.config`.
2. Start the pool again:
   ```powershell
   Start-WebAppPool -Name "MyAppPool"
   ```
3. If it's a custom identity whose password has changed, update the credentials:
   ```cmd
   appcmd set apppool "MyAppPool" /processModel.userName:DOMAIN\svc_app /processModel.password:<new>
   ```
4. **Temporary mitigation only.** You can raise the rapid-fail threshold or turn off rapid-fail protection so the pool keeps restarting while you investigate. Never leave it this way permanently.
5. Look at the recycling settings (`recycling.periodicRestart.privateMemory`) if pools are being recycled for memory and the limit is unrealistically low.

## Escalation

- Escalate to the application development team with the crash dump analysis if the faulting module belongs to the application.
- Escalate to the Windows platform team if the faulting module is an OS or IIS component (for example `iiscore.dll` or `ntdll.dll`) or if WAS itself is failing.
