# Walkthrough: Restore trident-protect-1 → trident-protect-1-restore

## Source
- Namespace: `trident-protect-1`
- App: nginx (stateless + PVC)
- Backup used: `hourly-dd824-20260722100000` (latest from 10:00 schedule)
- AppVault: `ontap-s3-appvault`

## Destination
- Namespace: `trident-protect-1-restore` (new)

## Steps

### Step 0: Modify original data (ก่อน restore - เพื่อพิสูจน์ว่า restore กลับค่าเดิม)
```bash
# เปลี่ยน content ใน index.html เป็น "BEFORE RESTORE"
oc exec -n trident-protect-1 deploy/nginx-app -- sh -c \
  'echo "BEFORE RESTORE" > /usr/share/nginx/html/index.html'

# Verify
oc exec -n trident-protect-1 deploy/nginx-app -- cat /usr/share/nginx/html/index.html
```
Expected: `BEFORE RESTORE`

> **⚠️ Caveat**: nginx deployment มี init-container `init-copy` ที่ copy `index.html` จาก ConfigMap ทุกครั้งที่ pod start ดังนั้นหลัง restore pod ใหม่จะ trigger init-copy → index.html จะกลับเป็น content จาก ConfigMap ไม่ใช่ค่าที่ backup เก็บ (แต่ demo visual ยังใช้ได้)

### Step 1: Create namespace + SCC
```bash
oc new-project trident-protect-1-restore
oc adm policy add-scc-to-user hostmount-anyuid -z default -n trident-protect-1-restore
```

### Step 2: Apply BackupRestore CR
```bash
oc apply -f 01-restore.yaml
```

### Step 3: Monitor restore status
```bash
oc get backuprestore restore-protect-1 -n trident-protect-1-restore -w
# รอ state = Completed (ใช้เวลา 1-3 นาที)
```

### Step 4: Verify restored resources
```bash
oc get all,pvc -n trident-protect-1-restore
```
คาดว่าจะเห็น:
- `deployment.apps/nginx-app`
- `service/nginx-svc`
- `route.route.openshift.io/nginx-route` (status = HostAlreadyClaimed)
- `persistentvolumeclaim/nginx-pvc`

### Step 5: Recreate Route (hostname conflict)
Route เดิมใช้ hostname ซ้ำกับต้นทาง ต้องลบแล้วสร้างใหม่ด้วย hostname ต่างกัน:
```bash
oc delete route nginx-route -n trident-protect-1-restore --ignore-not-found
cat <<EOF | oc apply -f -
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: nginx-route-restored
  namespace: trident-protect-1-restore
spec:
  host: nginx-route-restored-trident-protect-1.apps.clus1.ntapth.com
  port:
    targetPort: 8080
  to:
    kind: Service
    name: nginx-svc
EOF
```

### Step 6: Test route
```bash
ROUTE=$(oc get route nginx-route-restored -n trident-protect-1-restore -o jsonpath='{.spec.host}')
echo "URL: http://$ROUTE"
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" "http://$ROUTE"
curl -s "http://$ROUTE" | head -5
```
Expected: HTTP 200 + "test nginx trident-protect backup"

### Step 7: Verify proof - ข้อมูลกลับเป็นค่าเดิม (ไม่ใช่ "BEFORE RESTORE")
```bash
# ตรวจ index.html กลับเป็น content ต้นฉบับ
oc exec -n trident-protect-1-restore deploy/nginx-app -- cat /usr/share/nginx/html/index.html
```
Expected: `test nginx trident-protect backup` (ไม่ใช่ `BEFORE RESTORE`)

> **หมายเหตุ**: ถ้า content เป็น `test nginx trident-protect backup` = init-copy ทำงาน + restore สำเร็จ หากต้องการ conclusive proof ของ restore อย่างเดียว ให้ใช้ไฟล์อื่นที่ไม่อยู่ใน ConfigMap เช่น `marker.html`

## BackupRestore YAML ที่ใช้งานได้

ต้องมี 4 ฟิลด์นี้:
- `appArchivePath` — path ของ backup ใน AppVault
- `appVaultRef` — ชื่อ AppVault
- `backupName` — ชื่อ Backup CR
- `destinationApplicationName` — ตั้งชื่อ Application ใหม่ใน destination (หลีกเลี่ยง conflict)
- `namespaceMapping` — map source → destination
- `storageClassMapping` — map SC เก่าที่ไม่มี parameters → SC ใหม่

## Troubleshooting

### Error: "Existing app definition is different"
- ใช้ `destinationApplicationName` ตั้งชื่อใหม่

### Error: "host namespace not in includedNamespaces"
- BackupRestore CR ต้องอยู่ใน **destination namespace** (ไม่ใช่ source)

### Error: "backendType not found for storage class X"
- เพิ่ม `storageClassMapping` ถ้า SC ต้นทางไม่มี `parameters.backendType`

### Error: `appArchivePath: Required value`
- ดึง appArchivePath จาก backup status:
```bash
oc get backup <backup-name> -n <source-ns> -o jsonpath='{.status.appArchivePath}'
```

### Restore stuck in "InProgress"
```bash
oc describe backuprestore restore-protect-1 -n trident-protect-1-restore
oc logs -n trident-protect -l app=trident-protect-controller-manager --tail=50 | grep -i restore
```

### PVC not Bound
```bash
oc get pvc -n trident-protect-1-restore
oc describe pvc nginx-pvc -n trident-protect-1-restore
```

### Rollback (delete namespace)
```bash
oc delete project trident-protect-1-restore
```