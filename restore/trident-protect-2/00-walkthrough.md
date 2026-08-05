# Walkthrough: Restore trident-protect-2 → trident-protect-2-restore

## Source
- Namespace: `trident-protect-2`
- Apps: postgres (stateful) + webapp-query (Flask frontend)
- Backup used: `hourly-e9b9f-20260722100000` (latest from 10:00 schedule)
- AppVault: `ontap-s3-appvault`

## Destination
- Namespace: `trident-protect-2-restore` (new)

## Steps

### Step 0: Modify original data (ก่อน restore - เพิ่ม CVO row เพื่อพิสูจน์ restore)
```bash
# เพิ่ม NetApp CVO (Cloud Volumes ONTAP) - cloud storage model ใหม่ที่ไม่มีใน backup
oc exec -n trident-protect-2 postgres-0 -- psql -U postgres -d testdb -c "
INSERT INTO netapp_models (model, series, cpu_info, memory_gb, rack_units, max_raw_capacity_tb, power_watts, min_ontap)
VALUES ('CVO', 'Cloud Series', 'Cloud-based virtual storage', 0, 0, 0, 0, '9.12.1');"

# Verify count = 17 (จากเดิม 16)
oc exec -n trident-protect-2 postgres-0 -- psql -U postgres -d testdb -c "SELECT COUNT(*) FROM netapp_models;"
oc exec -n trident-protect-2 postgres-0 -- psql -U postgres -d testdb -c "SELECT * FROM netapp_models WHERE model='CVO';"
```
Expected:
- count: 17
- CVO row ปรากฏ: model=`CVO`, series=`Cloud Series`, min_ontap=`9.12.1`

> **⚠️ หมายเหตุ**: ต้องใส่ `0` ใน numeric fields (memory_gb, rack_units, max_raw_capacity_tb, power_watts) ไม่ใช่ `NULL` เพราะ webapp ใช้ `:,` format กับ None ไม่ได้ จะ error `unsupported format string passed to NoneType.__format__`

### Step 1: Create namespace + SCC
```bash
oc new-project trident-protect-2-restore
oc adm policy add-scc-to-user hostmount-anyuid -z default -n trident-protect-2-restore
```

### Step 2: Apply BackupRestore CR
```bash
oc apply -f 01-restore.yaml
```

### Step 3: Monitor restore status
```bash
oc get backuprestore restore-protect-2 -n trident-protect-2-restore -w
# รอ state = Completed (ใช้เวลา 2-5 นาที เพราะมี 2 deployments + PVC)
```

### Step 4: Verify restored resources
```bash
oc get all,pvc -n trident-protect-2-restore
```
คาดว่าจะเห็น:
- `statefulset.apps/postgres`
- `deployment.apps/webapp-query`
- `service/postgres-svc` (ClusterIP None = headless)
- `service/webapp-svc`
- `route.route.openshift.io/webapp-query-route` (status = HostAlreadyClaimed)
- `persistentvolumeclaim/postgres-pvc`

### Step 5: Recreate Route (hostname conflict)
Route เดิมใช้ hostname ซ้ำกับต้นทาง ต้องลบแล้วสร้างใหม่ด้วย hostname ต่างกัน:
```bash
oc delete route webapp-query-route -n trident-protect-2-restore --ignore-not-found
cat <<EOF | oc apply -f -
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: webapp-query-route-restored
  namespace: trident-protect-2-restore
spec:
  host: webapp-query-route-restored-trident-protect-2.apps.clus1.ntapth.com
  port:
    targetPort: 5000
  to:
    kind: Service
    name: webapp-svc
EOF
```

### Step 6: Test route + verify data
```bash
ROUTE=$(oc get route webapp-query-route-restored -n trident-protect-2-restore -o jsonpath='{.spec.host}')
echo "URL: http://$ROUTE"
curl -s -o /dev/null -w "HTTP Status: %{http_code}\n" "http://$ROUTE"
curl -s "http://$ROUTE" | head -10
```
Expected: HTTP 200 + ตาราง NetApp models 16 rows

### Step 7: Verify postgres data directly
**หมายเหตุ**: database ชื่อ `testdb` ไม่ใช่ `netapp`
```bash
oc exec -n trident-protect-2-restore postgres-0 -- psql -U postgres -d testdb -c "SELECT COUNT(*) FROM netapp_models;"
```
Expected: 16

### Step 8: Verify proof - CVO row ต้องหายไป + count = 16 (ไม่ใช่ 17)
```bash
# ตรวจว่า CVO row ที่เพิ่มหายไป (DB ถูก revert กลับเป็นตอน backup)
oc exec -n trident-protect-2-restore postgres-0 -- psql -U postgres -d testdb -c "SELECT COUNT(*) FROM netapp_models;"
oc exec -n trident-protect-2-restore postgres-0 -- psql -U postgres -d testdb -c "SELECT * FROM netapp_models WHERE model='CVO';"
```
Expected:
- count: 16 (ไม่ใช่ 17) ← **พิสูจน์ว่า DB ถูก restore**
- CVO query: 0 rows

## BackupRestore YAML ที่ใช้งานได้

ต้องมี 4 ฟิลด์นี้:
- `appArchivePath` — path ของ backup ใน AppVault
- `appVaultRef` — ชื่อ AppVault
- `backupName` — ชื่อ Backup CR
- `destinationApplicationName` — ตั้งชื่อ Application ใหม่ใน destination
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

### Postgres pod not starting
```bash
oc get pods -n trident-protect-2-restore
oc describe pod -n trident-protect-2-restore postgres-0
oc logs -n trident-protect-2-restore postgres-0
```

### Rollback (delete namespace)
```bash
oc delete project trident-protect-2-restore
```

### Cleanup: ลบ CVO row ที่เพิ่มใน Step 0 (ถ้าต้องการ revert ต้นทางกลับเป็นเหมือนเดิม)
```bash
# ลบ row CVO ออกจาก postgres ต้นทาง
oc exec -n trident-protect-2 postgres-0 -- psql -U postgres -d testdb -c "DELETE FROM netapp_models WHERE model='CVO';"

# Verify count กลับเป็น 16
oc exec -n trident-protect-2 postgres-0 -- psql -U postgres -d testdb -c "SELECT COUNT(*) FROM netapp_models;"
oc exec -n trident-protect-2 postgres-0 -- psql -U postgres -d testdb -c "SELECT * FROM netapp_models WHERE model='CVO';"
```
Expected:
- DELETE 1
- count: 16
- CVO query: 0 rows