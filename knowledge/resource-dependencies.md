# Resource dependency & attach/detach test patterns (confirmed domain knowledge)

> Confirmed creation-order prerequisites and attach/detach lifecycles the CRUD
> scenarios must exercise. Recorded in git so scenarios encode real dependency
> order (a create that omits a prerequisite is a wrong test, not a backend bug).
> Machine-readable prerequisite lists live in
> `regression/scenarios/dependencies.json:prerequisites`.

## Creation-order DAG (must build prerequisites first)

```
VPC
 └─ subnet            (subnet CIDR ⊂ VPC CIDR — see domain-constraints.md)
     ├─ security-group   (create BEFORE the consumer, then ATTACH)
     ├─ keypair          (create BEFORE virtual server / nodepool)
     ├─ port             (create standalone → map SG → attach to server NIC)
     ├─ block storage / volume   (create standalone → attach → detach → re-attach)
     └─ file / parallel-file storage  (pre-create → attach to K8s cluster)
```

- **Virtual server** prereqs: `vpc → subnet → security-group → keypair →
  (image + server-type lookup) → server`. The SG is attached at/after create;
  keypair must exist first.
- **Kubernetes (SKE) cluster** prereqs: `vpc → subnet → security-group →
  keypair → file/parallel storage → cluster (attach storage via volume_id) →
  nodepool`. The storage volume must exist BEFORE the cluster create.
- **Nodepool** is VS-like: its nodes need subnet + security-group (+ keypair);
  exercise SG/linked-resource attach/detach where the API allows.

## Attach/detach sub-lifecycles (the resource is pre-created, then cycled)

1. **Block storage ↔ server**: create volume standalone → `attach-volume` →
   use (qos / extend / update) → `detach-volume` → **re-attach** → detach →
   delete. The point is the volume OUTLIVES a single attach (create once,
   attach/detach many).
2. **Port (NIC) ↔ server**: `POST /v1/ports` (standalone) → map a
   security-group onto the port → `attach` the port to the server as a NIC →
   `detach` → delete. The port and its SG mapping exist independently of the
   server.
3. **Security-group ↔ server / cluster / port**: create SG first → attach to
   the consumer → detach → (SG persists, reusable).

## VS-like resources (apply the same pattern)

Any resource that hosts compute and attaches NIC/volume/SG follows the VS
pattern (pre-create SG/keypair/subnet/storage, attach at create, then
attach/detach cycles): **virtual server, bare-metal server, auto-scaling
launch-config/group, SKE nodepool**.

## Current coverage vs gaps (as of this writing)

| Pattern | compute-virtualserver-full | container-ske-cluster-nodepool |
|---|---|---|
| vpc→subnet→SG→keypair pre-create | ✅ (SG ×2) | ✅ |
| SG attach + detach | ✅ | ✅ (cluster security-groups) |
| block volume create→attach→detach→delete | ✅ (single cycle) | volume pre-created + attached to cluster |
| volume **re-attach** cycle | ❌ (only one attach/detach) | n/a |
| **standalone PORT → map SG → attach/detach** | ❌ **(gap — /v1/ports unused)** | n/a |
| file / **parallel-file** storage for cluster | n/a | ⚠️ uses `/v1/volumes` NFS, not `parallel-filestorage` |
| nodepool SG/subnet attach/detach | n/a | ⚠️ partial (linked-resources) |

## Proposed scenario work (to close the gaps)

1. **NEW `compute-port-lifecycle`** (or extend virtualserver-full): `POST /v1/ports`
   → `PUT /v1/ports/{id}` to map a security-group → attach the port to the
   server as a secondary NIC → detach → `DELETE /v1/ports/{id}`. Adopts the
   shared VPC/subnet; SG pre-created. (Closes the main gap.)
2. **Volume re-attach cycle** in virtualserver-full: after the first
   detach, attach the SAME volume again, then detach + delete — proving the
   volume is independent of one attachment.
3. **SKE parallel-filestorage**: pre-create via `parallel-filestorage` service
   and attach to the cluster (in addition to / instead of the NFS volume).
4. **Nodepool** SG/subnet attach-detach where the nodepool API supports it.

All additions ADOPT the shared VPC+subnet (parallel-safe) and follow the
optional + grouped + broad-expect_status coverage convention.
