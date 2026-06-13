"""One-shot wave5-net composition script (run from repo root; not part of the
suite — the artifact is regression/scenarios/lifecycles/generated__wave5-net.json)."""
import json
from regression.scenarios import composer

model = composer.load_model()

# 1) firewall via carrier: IGW with firewall_enabled=true -> lookup-on-list
#    firewall -> rule chain. Strip adopt stamps: the firewall lookup filters by
#    vpc_name=regrvpc{unique}, which only matches a SELF-CREATED vpc (the
#    session-shared VPC has a different name), and an IGW may already exist on
#    the shared VPC.
fw = composer.compose(["firewall", "firewall-rule"],
                      options={"internet-gateway": {"firewall_enabled": True}},
                      model=model, lifecycle_id="gen-wave5-fw")
for s in fw["steps"]:
    s.pop("adopt", None)
fw["enabled"] = True
fw["_note"] += (" | adopt stamps stripped on purpose: the firewall lookup "
                "(GET /v1/firewalls?vpc_name=regrvpc{unique}&product_type=IGW) "
                "only resolves against a self-created VPC, and IGW is 1:1 per "
                "VPC — must not collide with the session-shared VPC's IGW.")

# 2) vpc-endpoint wired to a real filestorage volume id (endpoint_type=FS).
vpce = composer.compose(["vpc-endpoint"], model=model,
                        lifecycle_id="gen-wave5-vpce")
vpce["enabled"] = True

# 3) private-nat over the transit-gateway branch (direct-connect blocked on
#    physical-line question). TGW is also a target so its verify reads run.
pnat = composer.compose(["private-nat", "transit-gateway"],
                        choices={"private-nat": "transit-gateway"},
                        model=model, lifecycle_id="gen-wave5-privnat")
pnat["enabled"] = True

# 4) LB members + setters + public static-NAT (needs IGW in the VPC, PF-13).
lbm = composer.compose(["load-balancer", "lb-health-check", "lb-server-group",
                        "lb-listener", "lb-member", "lb-member-bulk",
                        "lb-static-nat"],
                       model=model, lifecycle_id="gen-heavy-lb-members")
lbm["enabled"] = True
lbm["heavy"] = True  # load-balancer node is heavy (billable; ~LB provision time)
lbm["_note"] += (" | heavy: the loadbalancer itself is billable/slow; NO "
                 "backend VM needed — members register direct in-subnet IPs "
                 "(10.124.0.101/.102, shared-subnet re-homing rule).")

out = {
    "_comment": ("Composed wave 5 — networking enablement batch "
                 "(docs/SERVICE-GAP-REPORTS.md group 1, 2026-06-13): implicit "
                 "firewall via IGW firewall_enabled=true + rule chain; "
                 "vpc-endpoint wired to a real FS volume id; private-nat over "
                 "the verified transit-gateway branch; LB member/setter/"
                 "static-NAT coverage (heavy)."),
    "lifecycles": [fw, vpce, pnat, lbm],
}
path = "regression/scenarios/lifecycles/generated__wave5-net.json"
with open(path, "w") as f:
    json.dump(out, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("wrote", path)
for lc in out["lifecycles"]:
    print(f"  {lc['id']}: {len(lc['steps'])} steps; heavy={lc.get('heavy')}")
    for s in lc["steps"]:
        print("   -", s["name"])
