# Walkthrough: Restore trident-protect-4 (CentOS VM) → trident-protect-4-restore

## Source
- Namespace: `trident-protect-4`
- App: CentOS Stream 9 VM (centos-vm-1)
- Backup used: `centos-vm-backup` (manually triggered)
- AppVault: `ontap-s3-appvault`
- VM disk: DataVolume บน StorageClass `a400-nfs-v2`

## Destination
- Namespace: `trident-protect-4-restore` (new)
- VM ที่ถูกสร้าง: **`centos-vm-1`** (เหมือนต้นทาง เพราะ Trident Protect restore ตาม backup spec — `destinationApplicationName` คุมได้แค่ App CR ไม่ได้คุม VM name)

## Steps

### Step 0b: เลือก backup ที่ถูกต้อง (สำคัญ!)
> **⚠️ ต้องใช้ backup ที่มาจาก VM ที่ SSH ใช้งานได้**
>
> - ✅ ใช้ `custom-a9c26-20260722120000` (12:00 schedule) — จาก VM ที่ SSH ทำงาน
> - ❌ ห้ามใช้ `centos-vm-backup` (09:29 manual) — จาก VM เก่าก่อนสร้างใหม่ (SELinux issue)
>
> ดึง appArchivePath จาก backup ที่เลือก:
> ```bash
> oc get backup <backup-name> -n trident-protect-4 -o jsonpath='{.status.appArchivePath}'
> ```

### Step 1: Create namespace + SCC
```bash
oc new-project trident-protect-4-restore
oc adm policy add-scc-to-user hostmount-anyuid -z default -n trident-protect-4-restore
```

### Step 2: Apply BackupRestore CR
```bash
oc apply -f 01-restore.yaml
```

### Step 3: Monitor restore status
```bash
oc get backuprestore restore-protect-4 -n trident-protect-4-restore -w
# รอ state = Completed (ใช้เวลา 3-5 นาที เพราะมี VM disk 34GB)
```

### Step 4: Verify restored resources
```bash
oc get vm,vmi,dv,pvc -n trident-protect-4-restore
```
คาดว่าจะเห็น:
- `vm.kubevirt.io/centos-vm-1` (status = Running หรือ Stopped ขึ้นกับ backup)
- `vmi.kubevirt.io/centos-vm-1`
- `datavolume.cdi.kubevirt.io/centos-vm-1` (Succeeded)
- `persistentvolumeclaim/centos-vm-1` (Bound, a400-nfs-v2)

### Step 5: Start VM (ถ้ายังไม่ Running)
```bash
oc patch vm centos-vm-1 -n trident-protect-4-restore \
  --type merge -p '{"spec":{"runStrategy":"Always"}}'
```

### Step 6: Verify VM Running
```bash
oc get vm centos-vm-1 -n trident-protect-4-restore -w
# รอ Running + Ready=True (ใช้เวลา 1-2 นาที)
```
ดู IP ใหม่:
```bash
oc get vmi centos-vm-1 -n trident-protect-4-restore \
  -o jsonpath='{.status.interfaces[0].ipAddress}'
```

### Step 7: Verify SSH
ใช้ SSH key ที่อยู่ใน backup (cloud-init inject ไว้ตอนสร้าง VM):
```bash
virtctl ssh centos@vmi/centos-vm-1 -n trident-protect-4-restore \
  -i /root/.ssh/id_rsa -c "whoami; hostname; cat /etc/redhat-release; ls /home/centos/"
```
Expected:
- user: `centos`
- hostname: `centos-vm-1` (เหมือนต้นทาง)
- OS: CentOS Stream release 9

> **⚠️ Known Issue**: VM ที่ restore จาก Trident Protect อาจไม่มี SSH key ใน `authorized_keys` (cloud-init marker file มีอยู่แล้วใน disk → cloud-init ไม่ re-run) **Workaround**: ลบ VM ที่ restore + สร้างใหม่ด้วย cloud-init SSH key (ดู Workaround section ด้านล่าง)

### Step 8: Verify original VM ไม่ถูกแก้
```bash
oc get vm centos-vm-1 -n trident-protect-4
oc get vmi centos-vm-1 -n trident-protect-4
virtctl ssh centos@vmi/centos-vm-1 -n trident-protect-4 \
  -i /root/.ssh/id_rsa -c "hostname"
```
Expected: VM ต้นฉบับยัง Running อยู่, hostname = `centos-vm-1` (ทั้งสอง VM ชื่อเหมือนกัน แต่อยู่คนละ namespace)

## Workaround: Recreate VM ด้วย SSH key

ถ้า Step 7 SSH ไม่ได้ ให้ลบ VM + PVC ที่ restore แล้วสร้างใหม่:
```bash
# ลบ VM/PVC ที่ restore ได้แต่ SSH เข้าไม่ได้
oc delete vm centos-vm-1 -n trident-protect-4-restore
oc delete pvc centos-vm-1 -n trident-protect-4-restore

# สร้างใหม่ด้วย cloud-init SSH key
PUBKEY=$(cat /root/.ssh/id_rsa.pub)
cat <<EOF | oc apply -f -
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: centos-vm-1
  namespace: trident-protect-4-restore
  labels:
    app: centos-vm-1
spec:
  dataVolumeTemplates:
  - apiVersion: cdi.kubevirt.io/v1beta1
    kind: DataVolume
    metadata:
      name: centos-vm-1
    spec:
      sourceRef:
        kind: DataSource
        name: centos-stream9
        namespace: openshift-virtualization-os-images
      storage:
        resources:
          requests:
            storage: 30Gi
        storageClassName: a400-nfs-v2
        accessModes:
          - ReadWriteMany
  runStrategy: Always
  template:
    spec:
      domain:
        cpu:
          cores: 1
          sockets: 1
          threads: 1
        devices:
          disks:
          - disk: {bus: virtio}
            name: rootdisk
          - disk: {bus: virtio}
            name: cloudinitdisk
          interfaces:
          - masquerade: {}
            name: default
        memory:
          guest: 2Gi
      networks:
      - name: default
        pod: {}
      volumes:
      - dataVolume: {name: centos-vm-1}
        name: rootdisk
      - cloudInitNoCloud:
          userData: |
            #cloud-config
            user: centos
            password: centos123
            chpasswd: { expire: False }
            ssh_pwauth: True
            ssh_authorized_keys:
              - ${PUBKEY}
        name: cloudinitdisk
EOF

# รอ VM Running แล้วทดสอบ SSH
sleep 90
virtctl ssh centos@vmi/centos-vm-1 -n trident-protect-4-restore \
  -i /root/.ssh/id_rsa -c "whoami; hostname"
```

## BackupRestore YAML ที่ใช้งานได้

ต้องมีฟิลด์นี้:
- `appArchivePath` — path ของ backup ใน AppVault (ดึงจาก backup status)
- `appVaultRef` — ชื่อ AppVault
- `backupName` — ชื่อ Backup CR
- `destinationApplicationName` — ตั้งชื่อ Application ใหม่ใน destination
- `namespaceMapping` — map source → destination

> **ไม่ต้อง** `storageClassMapping` เพราะ PVC ต้นทางใช้ `a400-nfs-v2` อยู่แล้ว (มี `snapshots: "true"`)

> **หมายเหตุ**: `destinationApplicationName` คุมได้แค่ชื่อ **Application CR** เท่านั้น ไม่ได้เปลี่ยนชื่อ VM, VMI, DataVolume, PVC — ทุก resource จะ restore ด้วยชื่อเดิมตามที่อยู่ใน backup

## Troubleshooting

### Error: "Existing app definition is different"
- ใช้ `destinationApplicationName` ตั้งชื่อใหม่

### Error: "host namespace not in includedNamespaces"
- BackupRestore CR ต้องอยู่ใน **destination namespace**

### Error: "backendType not found for storage class X"
- เพิ่ม `storageClassMapping` ถ้า SC ต้นทางไม่มี `parameters.backendType`

### Error: `appArchivePath: Required value`
- ดึง appArchivePath จาก backup status:
```bash
oc get backup <backup-name> -n <source-ns> -o jsonpath='{.status.appArchivePath}'
```

### VM ไม่ start หลัง restore
```bash
oc describe vm centos-vm-1 -n trident-protect-4-restore
oc get events -n trident-protect-4-restore --sort-by='.lastTimestamp' | tail -20
```

### SSH ไม่ได้ (Known Issue)
- VM ที่ restore จาก Trident Protect อาจไม่มี SSH key ใน `authorized_keys`
- **Workaround**: ลบ VM + PVC แล้วสร้างใหม่ด้วย cloud-init (ดู Workaround section)

### Rollback (delete namespace)
```bash
oc delete project trident-protect-4-restore
```