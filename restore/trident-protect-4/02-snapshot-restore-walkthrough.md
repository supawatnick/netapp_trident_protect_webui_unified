# Walkthrough: Snapshot Restore for trident-protect-4 (VM)

## Source
- Namespace: `trident-protect-4`
- App: CentOS Stream 9 VM (`centos-vm-1`)
- Snapshot used: `custom-a9c26-20260722160000` (16:00 schedule snapshot)
- AppVault: `ontap-s3-appvault`
- VM disk: DataVolume บน StorageClass `a400-nfs-v2` (รองรับ snapshot)

## Destination
- Namespace: `trident-protect-4-restore-snap` (new)
- VM ที่ถูกสร้าง: `centos-vm-1` (เหมือนต้นทาง — `destinationApplicationName` คุมได้แค่ App CR)

## Snapshot vs Backup — ความแตกต่าง

| Item | Backup Restore | Snapshot Restore |
|------|----------------|-------------------|
| **CRD** | `BackupRestore` | `SnapshotRestore` |
| **Data source** | S3 bucket (Kopia) | CSI volume snapshot + S3 metadata |
| **Speed (VM 32GB)** | 3-5 นาที (download 32GB) | < 1 นาที ⚡ |
| **Cross-cluster** | ✓ (DR) | ✗ (same cluster only) |
| **Storage cost** | ใช้ S3 | ใช้ CSI snapshot space (minimal) |
| **Use case** | DR, long-term archive | Fast restore, dev/test |

> **สำหรับ VM**: snapshot restore เร็วกว่า 5-10 เท่า เพราะไม่ download 32GB disk image — ใช้ CSI snapshot ของ ONTAP แทน

## Prerequisite
- Snapshot CR ต้องมีอยู่ใน source namespace (มีอยู่แล้วจาก schedule)
- StorageClass ต้องมี `snapshots: "true"` parameter (`a400-nfs-v2` มี)
- VolumeSnapshotClass `csi-snapclass` ต้องมี (มีอยู่แล้ว)

## Steps

### Step 0b: เลือก Snapshot ที่ถูกต้อง (สำคัญ!)
> ใช้ Snapshot ที่ VM มี authorized_keys → SSH ทำงานได้

ตรวจ Snapshot ที่มี:
```bash
oc get snapshot -n trident-protect-4
# เลือก snapshot ล่าสุด
SNAPSHOT_NAME="custom-a9c26-20260722160000"

# ดึง appArchivePath
oc get snapshot $SNAPSHOT_NAME -n trident-protect-4 \
  -o jsonpath='{.status.appArchivePath}'
```

### Step 1: Create destination namespace + SCC
```bash
oc new-project trident-protect-4-restore-snap
oc adm policy add-scc-to-user hostmount-anyuid -z default -n trident-protect-4-restore-snap
```

### Step 2: Apply SnapshotRestore CR
```bash
oc apply -f 02-snapshot-restore.yaml
```

### Step 3: Monitor status
```bash
oc get snapshotrestore snapshot-restore-protect-4 -n trident-protect-4-restore-snap -w
# รอ state = Completed (ใช้เวลา < 1 นาที เพราะไม่ download disk)
```

### Step 4: Verify restored resources
```bash
oc get vm,vmi,pvc -n trident-protect-4-restore-snap
```
คาดว่าจะเห็น:
- `vm.kubevirt.io/centos-vm-1` (Running)
- `vmi.kubevirt.io/centos-vm-1`
- `persistentvolumeclaim/centos-vm-1` (Bound, a400-nfs-v2)

### Step 5: Verify VM Running
```bash
oc get vm centos-vm-1 -n trident-protect-4-restore-snap
# STATUS = Running, READY = True

# ดู IP ใหม่
oc get vmi centos-vm-1 -n trident-protect-4-restore-snap \
  -o jsonpath='{.status.interfaces[0].ipAddress}'
```

### Step 6: Verify SSH
```bash
virtctl ssh centos@vmi/centos-vm-1 -n trident-protect-4-restore-snap \
  -i /root/.ssh/id_rsa -c "whoami; hostname; cat /etc/redhat-release; ls -la /home/centos/.ssh/"
```
Expected:
- user: `centos`
- hostname: `centos-vm-1`
- OS: CentOS Stream release 9
- authorized_keys มีอยู่ (modtime 06:05)

### Step 7: Verify original VM ไม่ถูกแก้
```bash
oc get vm centos-vm-1 -n trident-protect-4
oc get vmi centos-vm-1 -n trident-protect-4
virtctl ssh centos@vmi/centos-vm-1 -n trident-protect-4 \
  -i /root/.ssh/id_rsa -c "hostname"
```
Expected: VM ต้นฉบับยัง Running, hostname = `centos-vm-1`

## Performance Demo

ลองเทียบเวลาระหว่าง BackupRestore กับ SnapshotRestore:

```bash
# Snapshot restore ใช้เวลา
time oc get snapshotrestore snapshot-restore-protect-4 \
  -n trident-protect-4-restore-snap -o jsonpath='{.status.completionTimestamp}'

# Backup restore ใช้เวลา
time oc get backuprestore restore-protect-4 \
  -n trident-protect-4-restore -o jsonpath='{.status.completionTimestamp}'
```

> Snapshot restore ควรเร็วกว่า 5-10 เท่า สำหรับ VM disk 32GB

## SnapshotRestore YAML

```yaml
apiVersion: protect.trident.netapp.io/v1
kind: SnapshotRestore
metadata:
  name: snapshot-restore-protect-4
  namespace: trident-protect-4-restore-snap
spec:
  appArchivePath: <ดึงจาก Snapshot status>
  appVaultRef: ontap-s3-appvault
  snapshotName: <ชื่อ Snapshot CR>
  destinationApplicationName: <ตั้งชื่อ App ใหม่>
  namespaceMapping:
    - source: trident-protect-4
      destination: trident-protect-4-restore-snap
```

ต้องมีฟิลด์:
- `appArchivePath` — path ของ snapshot ใน AppVault
- `appVaultRef` — ชื่อ AppVault
- `snapshotName` — ชื่อ Snapshot CR
- `destinationApplicationName` — ตั้งชื่อ Application ใหม่ใน destination
- `namespaceMapping` — map source → destination

> **ไม่ต้อง** `storageClassMapping` เถ้า PVC ต้นทางใช้ `a400-nfs-v2`

## Troubleshooting

### Error: "Existing app definition is different"
- ใช้ `destinationApplicationName` ตั้งชื่อใหม่

### Error: "host namespace not in includedNamespaces"
- SnapshotRestore CR ต้องอยู่ใน **destination namespace**

### Error: `appArchivePath: Required value`
- ดึง appArchivePath จาก Snapshot status:
```bash
oc get snapshot <snapshot-name> -n trident-protect-4 -o jsonpath='{.status.appArchivePath}'
```

### Snapshot ไม่มี
```bash
oc get snapshot -n trident-protect-4
# ถ้าว่าง → สร้าง manual snapshot หรือรอ schedule
oc apply -f - <<EOF
apiVersion: protect.trident.netapp.io/v1
kind: Snapshot
metadata:
  name: ondemand-snapshot
  namespace: trident-protect-4
spec:
  applicationRef: centos-vm-app
  appVaultRef: ontap-s3-appvault
EOF
```

### VolumeSnapshot ไม่ทำงาน
```bash
oc get volumesnapshotclass
# ต้องเห็น csi-snapclass

oc get sc a400-nfs-v2 -o jsonpath='{.parameters.snapshots}'
# ต้องเป็น "true"
```

### SSH ไม่ได้ (Snapshot มาจาก VM ที่ SSH ไม่ทำงาน)
- ลบ VM/PVC ใน destination → สร้างใหม่ด้วย cloud-init SSH key

### Rollback (delete namespace)
```bash
oc delete project trident-protect-4-restore-snap
```