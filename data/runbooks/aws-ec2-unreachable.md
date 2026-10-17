# AWS EC2 Instance Unreachable

**Platform:** AWS EC2 (Linux and Windows)
**Severity:** High — the service hosted on the instance is down, or no one can get in to administer it.

## Symptoms

- SSH (22) or RDP (3389) connections time out or are refused.
- The application endpoint on the instance doesn't respond.
- The EC2 console shows **status check failures** (`1/2 checks passed` or `0/2`).
- CloudWatch alarm on `StatusCheckFailed`, `StatusCheckFailed_System` or `StatusCheckFailed_Instance`.

## Likely Causes

1. **Network path:** a security group or network ACL blocks the port, the route table has no route to an internet gateway or NAT, or the instance has no public IP or Elastic IP.
2. **System status check failure:** a problem on the AWS side, such as host hardware or loss of network connectivity.
3. **Instance status check failure:** a problem inside the OS, such as a kernel panic, a full root disk, a corrupted fstab, bad network config or exhausted memory.
4. The service itself is down, for example sshd, the RDP service or the application process stopped.
5. The host firewall (iptables, firewalld or Windows Firewall) blocks the port.
6. The instance was stopped, terminated or replaced by an Auto Scaling group.

## Diagnosis Steps

### 1. Check the instance state and status checks

```bash
aws ec2 describe-instances --instance-ids i-0123456789abcdef0 \
  --query "Reservations[].Instances[].{State:State.Name,PublicIp:PublicIpAddress,PrivateIp:PrivateIpAddress,Subnet:SubnetId,SGs:SecurityGroups[].GroupId}"

aws ec2 describe-instance-status --instance-ids i-0123456789abcdef0 --include-all-instances
```

- `SystemStatus` impaired → AWS infrastructure problem (go to the Resolution section).
- `InstanceStatus` impaired → OS-level problem.

### 2. Check the network path

```bash
aws ec2 describe-security-groups --group-ids sg-0abc123 \
  --query "SecurityGroups[].IpPermissions"

aws ec2 describe-network-acls --filters Name=association.subnet-id,Values=subnet-0abc123

aws ec2 describe-route-tables --filters Name=association.subnet-id,Values=subnet-0abc123
```

Things to check:

- The inbound security group rule allows the port from your source IP.
- The NACL allows both the inbound port **and** the outbound ephemeral ports (1024–65535). NACLs are stateless.
- The route table sends `0.0.0.0/0` to an `igw-` (public subnet) or a `nat-` gateway (private subnet).

For an automated check, run **VPC Reachability Analyzer** from your source to the instance ENI.

### 3. Read the console output and screenshot

```bash
aws ec2 get-console-output --instance-id i-0123456789abcdef0 --latest --output text
aws ec2 get-console-screenshot --instance-id i-0123456789abcdef0 --query ImageData --output text | base64 -d > screen.jpg
```

Look for kernel panics, `No space left on device`, fstab mount errors, or a Windows stuck at "Applying updates".

### 4. Get in without SSH or RDP

- **SSM Session Manager.** This needs the SSM agent and an instance profile with `AmazonSSMManagedInstanceCore`.
  ```bash
  aws ssm start-session --target i-0123456789abcdef0
  ```
- **EC2 Serial Console.** Works on Nitro instances, and must be enabled at the account level. Use it when networking is broken inside the OS.

### 5. Once you're in, check the OS

```bash
df -h                      # full disk?
free -m                    # memory exhaustion?
systemctl status sshd      # sshd running?
sudo iptables -L -n        # host firewall
journalctl -p err -b       # boot errors
```

## Resolution

| Cause | Action |
|---|---|
| System status check failed | Stop and then start the instance (not a reboot) so it moves to new hardware. Instance store data is lost. |
| Security group / NACL / route | Add the missing rule or route. Use least privilege and limit the source CIDR. |
| No public IP | Attach an Elastic IP, or connect through a bastion host, VPN or SSM. |
| Root disk full | Free up space through SSM, or grow the EBS volume (`modify-volume`) and then run `growpart` and `resize2fs`/`xfs_growfs`. |
| Broken fstab / OS won't boot | Stop the instance, detach the root volume, attach it to a rescue instance, fix the files, reattach it and start the instance. Or use **EC2Rescue**. |
| sshd / service stopped | Restart it through SSM (`sudo systemctl restart sshd`) and find out why it stopped. |

## Escalation

- If the system status check is still failing after a stop/start, open an AWS Support case with the instance ID and the times of the failures.
- If the instance belongs to an Auto Scaling group, look at the ASG activity history before making changes, because the ASG may replace the instance.
- Escalate to the network team for Transit Gateway, VPN or Direct Connect paths.
