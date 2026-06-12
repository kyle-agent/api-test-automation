"""Offline tests for regression/scenarios/composer.py (R2a, contract C2).

Self-contained fixture model (contract C6 — R1's knowledge/formal/resources
may not exist yet): ~12 nodes mirroring the plan's nw-vpc networking bundle,
including an enum option (vpc-endpoint), count:2 (vpc-peering), a one_of with
bind/use + an optional ref option (privatelink-service), and a cost-asymmetric
one_of (nat: igw vs load-balancer).

Documented deterministic cidr scheme under test:
  unique-block  -> 10.<160+k>.0.0/20 in allocation order
  sub-block-of  -> the (8+j)-th /24 of the parent instance's block
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from regression.scenarios import composer
from regression.scenarios.composer import ComposeError, compose, plan

FIXTURE_YAML = """
version: 1
resources:
  vpc:
    code: nw-vpc-vpc
    service: networking/vpc
    adopt: vpc
    requires: []
    create:
      endpoint: "POST /v1/vpcs"
      body: {name: "regrvpc{ualpha}", cidr: "{opt.cidr}", tags: []}
      options:
        cidr: {type: cidr, required: true, pick: unique-block}
    capture: {vpc_id: "$.vpc.id"}
    ready: {field: "$.vpc.state", until: ACTIVE, timeout: 180, interval: 10}
    delete: {endpoint: "DELETE /v1/vpcs/{vpc_id}", destructive: true}
    quota: vpc
    provenance: VALIDATED

  subnet:
    code: nw-vpc-subnet
    service: networking/vpc
    adopt: subnet
    requires: [vpc]
    create:
      endpoint: "POST /v1/subnets"
      body: {name: "regrsub{ualpha}", vpc_id: "{vpc.vpc_id}",
             cidr: "{opt.cidr}", type: GENERAL}
      options:
        cidr: {type: cidr, required: true, pick: sub-block-of, of: vpc.cidr}
    capture: {subnet_id: "$.subnet.id"}
    ready: {field: "$.subnet.state", until: ACTIVE}
    delete: {endpoint: "DELETE /v1/subnets/{subnet_id}", destructive: true}
    quota: subnet

  security-group:
    code: nw-vpc-sg
    service: networking/security-group
    requires: []
    create:
      endpoint: "POST /v1/security-groups"
      body: {name: "regrsg{ualpha}", loggable: false}
    capture: {sg_id: "$.security_group.id"}
    delete: {endpoint: "DELETE /v1/security-groups/{sg_id}",
             destructive: true}
    quota: security-group

  public-ip:
    code: nw-vpc-public-ip
    service: networking/vpc
    requires: []
    create:
      endpoint: "POST /v1/public-ips"
      body: {type: STATIC}
    capture: {public_ip_id: "$.public_ip.id"}
    delete: {endpoint: "DELETE /v1/public-ips/{public_ip_id}",
             destructive: true}

  igw:
    code: nw-vpc-igw
    service: networking/vpc
    requires: [vpc]
    create:
      endpoint: "POST /v1/internet-gateways"
      body: {vpc_id: "{vpc.vpc_id}"}
    capture: {igw_id: "$.internet_gateway.id"}
    ready: {field: "$.internet_gateway.state", until: ATTACHED}
    delete: {endpoint: "DELETE /v1/internet-gateways/{igw_id}",
             destructive: true}

  port:
    code: nw-vpc-port
    service: networking/vpc
    requires: [subnet]
    create:
      endpoint: "POST /v1/ports"
      body: {name: "regrport{ualpha}", subnet_id: "{subnet.subnet_id}"}
    capture: {port_id: "$.port.id"}
    delete: {endpoint: "DELETE /v1/ports/{port_id}", destructive: true}

  load-balancer:
    code: nw-vpc-lb
    service: networking/loadbalancer
    heavy: true
    requires: [subnet]
    create:
      endpoint: "POST /v1/load-balancers"
      body: {name: "regrlb{ualpha}", subnet_id: "{subnet.subnet_id}"}
    capture: {lb_id: "$.load_balancer.id"}
    ready: {field: "$.load_balancer.state", until: ACTIVE}
    delete: {endpoint: "DELETE /v1/load-balancers/{lb_id}",
             destructive: true}
    quota: lb

  server:
    code: nw-vpc-server
    service: compute/virtualserver
    requires: [subnet]
    create:
      endpoint: "POST /v1/servers"
      body: {name: "regrsrv{ualpha}", subnet_id: "{subnet.subnet_id}"}
    capture: {server_id: "$.server.id", server_ip: "$.server.ip"}
    ready: {field: "$.server.state", until: ACTIVE}
    delete: {endpoint: "DELETE /v1/servers/{server_id}", destructive: true}
    quota: server

  vpc-endpoint:
    code: nw-vpc-endpoint
    service: networking/vpc
    requires: [vpc, subnet]
    create:
      endpoint: "POST /v1/vpc-endpoints"
      body: {name: "regrvpce{ualpha}", vpc_id: "{vpc.vpc_id}",
             subnet_id: "{subnet.subnet_id}", resource_type: "{opt.target}"}
      options:
        target: {type: enum, values: [dns, objectstorage, filestorage, scr],
                 required: true, vary: true}
    capture: {vpc_endpoint_id: "$.vpc_endpoint.id"}
    delete: {endpoint: "DELETE /v1/vpc-endpoints/{vpc_endpoint_id}",
             destructive: true}

  vpc-peering:
    code: nw-vpc-peering
    service: networking/vpc
    requires:
      - {ref: vpc, count: 2}
    create:
      endpoint: "POST /v1/vpc-peerings"
      body: {name: "regrpeer{ualpha}", requester_vpc_id: "{vpc.vpc_id}",
             approver_vpc_id: "{vpc.2.vpc_id}", tags: []}
    capture: {vpc_peering_id: "$.vpc_peering.id"}
    ready: {field: "$.vpc_peering.state", until: ACTIVE}
    delete: {endpoint: "DELETE /v1/vpc-peerings/{vpc_peering_id}",
             destructive: true}
    quota: peering

  privatelink-service:
    code: nw-vpc-privatelink-svc
    service: networking/vpc
    requires:
      - subnet
      - one_of: [load-balancer, {ref: server, use: server_ip}]
        bind: backend
    create:
      endpoint: "POST /v1/privatelink-services"
      body: {name: "regrpls{ualpha}", subnet_id: "{subnet.subnet_id}",
             backend: "{dep.backend}",
             security_group_id: "{opt.security_group}"}
      options:
        security_group: {type: ref, target: security-group, required: false}
    capture: {pls_id: "$.privatelink_service.id"}
    ready: {field: "$.privatelink_service.state", until: ACTIVE}
    delete: {endpoint: "DELETE /v1/privatelink-services/{pls_id}",
             destructive: true}

  nat:
    code: nw-vpc-nat
    service: networking/vpc
    requires:
      - one_of: [igw, load-balancer]
        bind: uplink
    create:
      endpoint: "POST /v1/nat-gateways"
      body: {name: "regrnat{ualpha}", uplink_id: "{dep.uplink}"}
    capture: {nat_id: "$.nat_gateway.id"}
    delete: {endpoint: "DELETE /v1/nat-gateways/{nat_id}",
             destructive: true}
"""


@pytest.fixture(scope="module")
def model():
    return yaml.safe_load(FIXTURE_YAML)["resources"]


def _names(lc):
    return [s["name"] for s in lc["steps"]]


def _step(lc, name):
    return next(s for s in lc["steps"] if s["name"] == name)


# ---------------------------------------------------------------------------
# single target: closure + order + teardown reversal

def test_single_target_closure_order_teardown(model):
    p = plan(["port"], model=model)
    assert p["order"] == ["vpc", "subnet", "port"]
    assert p["teardown"] == ["port", "subnet", "vpc"]

    lc = compose(["port"], model=model)
    assert lc["id"] == "gen-port"
    assert lc["service"] == "networking/vpc"
    assert lc["enabled"] is False  # drafts are never auto-enabled (C4)
    assert _names(lc) == [
        "create-vpc", "wait-vpc", "create-subnet", "wait-subnet",
        "create-port", "verify-port",
        "delete-port", "delete-subnet", "delete-vpc"]

    # capture wiring: subnet create consumes the vpc capture var
    assert _step(lc, "create-subnet")["json"]["vpc_id"] == "{vpc_id}"
    assert _step(lc, "create-port")["json"]["subnet_id"] == "{subnet_id}"
    # ready -> poll step
    wait = _step(lc, "wait-vpc")
    assert wait["poll"] == {"field": "$.vpc.state", "until": ["ACTIVE"],
                            "timeout": 180, "interval": 10}
    assert wait["path"] == "/v1/vpcs/{vpc_id}"  # derived from delete endpoint
    # teardown is destructive
    for n in ("delete-port", "delete-subnet", "delete-vpc"):
        assert _step(lc, n)["destructive"] is True
    # adopt markers survive on the shared vpc/subnet
    assert _step(lc, "create-vpc")["adopt"] == "vpc"
    assert _step(lc, "delete-subnet")["adopt"] == "subnet"


# ---------------------------------------------------------------------------
# option substitution: cidr derivation + enum

def test_cidr_unique_block_and_sub_block(model):
    lc = compose(["subnet"], model=model)
    assert _step(lc, "create-vpc")["json"]["cidr"] == "10.160.0.0/20"
    # sub-block-of: 8th /24 inside the parent's allocated block
    assert _step(lc, "create-subnet")["json"]["cidr"] == "10.160.8.0/24"


def test_cidr_explicit_option_wins_and_drives_derivation(model):
    lc = compose(["subnet"], options={"vpc": {"cidr": "10.10.0.0/20"}},
                 model=model)
    assert _step(lc, "create-vpc")["json"]["cidr"] == "10.10.0.0/20"
    assert _step(lc, "create-subnet")["json"]["cidr"] == "10.10.8.0/24"


def test_enum_option_default_explicit_invalid(model):
    lc = compose(["vpc-endpoint"], model=model)
    assert _step(lc, "create-vpc-endpoint")["json"]["resource_type"] == "dns"

    lc = compose(["vpc-endpoint"], options={"vpc-endpoint": {"target": "scr"}},
                 model=model)
    assert _step(lc, "create-vpc-endpoint")["json"]["resource_type"] == "scr"

    with pytest.raises(ComposeError, match="not in enum"):
        compose(["vpc-endpoint"], options={"vpc-endpoint": {"target": "bogus"}},
                model=model)


# ---------------------------------------------------------------------------
# one_of: cheapest default / light-over-heavy / explicit / in-bundle

def test_one_of_default_is_light_branch(model):
    # lb and server branches cost the same (3 transitive creates) — the
    # light (non-heavy) server branch wins the tie-break
    p = plan(["privatelink-service"], model=model)
    assert p["branches"] == {"privatelink-service": "server"}
    assert "server" in p["order"] and "load-balancer" not in p["order"]

    lc = compose(["privatelink-service"], model=model)
    # use: server_ip — the bind substitutes the named capture, not the id
    assert _step(lc, "create-privatelink-service")["json"]["backend"] \
        == "{server_ip}"


def test_one_of_default_is_cheapest_branch(model):
    # nat: igw closure = 2 creates vs load-balancer closure = 3 -> igw,
    # even though both are non-heavy
    p = plan(["nat"], model=model)
    assert p["branches"] == {"nat": "igw"}
    lc = compose(["nat"], model=model)
    assert _step(lc, "create-nat")["json"]["uplink_id"] == "{igw_id}"


def test_one_of_explicit_choice(model):
    p = plan(["privatelink-service"],
             choices={"privatelink-service": "load-balancer"}, model=model)
    assert p["branches"] == {"privatelink-service": "load-balancer"}
    assert "server" not in p["order"]

    lc = compose(["privatelink-service"],
                 choices={"privatelink-service": "load-balancer"},
                 model=model)
    assert _step(lc, "create-privatelink-service")["json"]["backend"] \
        == "{lb_id}"

    with pytest.raises(ComposeError, match="not a one_of branch"):
        plan(["privatelink-service"],
             choices={"privatelink-service": "igw"}, model=model)


def test_one_of_in_bundle_preference(model):
    # §2.5 rule 4: lb is itself a bundle target, so privatelink takes the
    # lb branch even though the server branch is the default winner
    p = plan(["privatelink-service", "load-balancer"], model=model)
    assert p["branches"] == {"privatelink-service": "load-balancer"}
    assert "server" not in p["order"]
    # and load-balancer is created exactly once (target ∩ prerequisite)
    lc = compose(["privatelink-service", "load-balancer"], model=model)
    assert _names(lc).count("create-load-balancer") == 1


# ---------------------------------------------------------------------------
# optional ref option

def test_ref_option_absent_drops_key_present_pulls_node(model):
    lc = compose(["privatelink-service"], model=model)
    assert "security_group_id" not in _step(
        lc, "create-privatelink-service")["json"]
    assert "create-security-group" not in _names(lc)

    lc = compose(["privatelink-service"],
                 options={"privatelink-service": {"security_group": True}},
                 model=model)
    assert _step(lc, "create-privatelink-service")["json"][
        "security_group_id"] == "{sg_id}"
    names = _names(lc)
    assert names.count("create-security-group") == 1
    assert names.index("create-security-group") \
        < names.index("create-privatelink-service")
    assert names.index("delete-security-group") \
        > names.index("delete-privatelink-service")


# ---------------------------------------------------------------------------
# count: 2 — shared instance counts as 1, extras get suffixed vars

def test_count_two_with_shared_instance(model):
    p = plan(["vpc-peering"], model=model)
    assert p["instances"]["vpc"] == 2
    assert p["order"] == ["vpc", "vpc#2", "vpc-peering"]
    assert p["teardown"] == ["vpc-peering", "vpc#2", "vpc"]
    assert p["peak_quota"] == {"peering": 1, "vpc": 2}

    lc = compose(["vpc-peering"], model=model)
    names = _names(lc)
    assert names.count("create-vpc") == 1 and names.count("create-vpc-2") == 1
    # per-instance capture suffix + wiring into the peering body
    assert _step(lc, "create-vpc-2")["capture"] == {"vpc_id_2": "$.vpc.id"}
    body = _step(lc, "create-vpc-peering")["json"]
    assert body["requester_vpc_id"] == "{vpc_id}"
    assert body["approver_vpc_id"] == "{vpc_id_2}"
    # distinct deterministic cidr blocks per instance
    assert _step(lc, "create-vpc")["json"]["cidr"] == "10.160.0.0/20"
    assert _step(lc, "create-vpc-2")["json"]["cidr"] == "10.161.0.0/20"
    # adopt marks only the shared instance; the extra self-creates
    assert _step(lc, "create-vpc")["adopt"] == "vpc"
    assert "adopt" not in _step(lc, "create-vpc-2")
    # extra is deleted before the shared one
    assert names.index("delete-vpc-2") < names.index("delete-vpc")


def test_count_shared_across_bundle(model):
    # peering needs 2 vpcs; everything else shares instance 1 -> pool 2, not 3
    p = plan(["vpc-peering", "subnet"], model=model)
    assert p["instances"]["vpc"] == 2
    assert p["peak_quota"]["vpc"] == 2


# ---------------------------------------------------------------------------
# bundle composition: dedup + interval teardown + grafted verify

BUNDLE = ["vpc", "subnet", "security-group", "public-ip", "igw", "port",
          "load-balancer", "server", "vpc-endpoint", "vpc-peering",
          "privatelink-service", "nat"]


def test_bundle_dedup_vpc_created_once(model):
    p = plan(BUNDLE, model=model)
    lc = compose(BUNDLE, model=model)
    names = _names(lc)
    # 12 targets -> ONE create-vpc (plus the peering extra), ONE create-subnet
    assert names.count("create-vpc") == 1
    assert names.count("create-subnet") == 1
    assert names.count("create-vpc-2") == 1  # peering extra only
    # dedup report names the consumers of each shared prerequisite
    assert set(p["dedup"]["vpc"]) == {"subnet", "igw", "vpc-endpoint",
                                      "vpc-peering"}
    assert set(p["dedup"]["subnet"]) == {"port", "load-balancer", "server",
                                         "vpc-endpoint",
                                         "privatelink-service"}
    # in-bundle one_of: both groups resolve to nodes already in the bundle
    assert p["branches"] == {"nat": "igw",
                             "privatelink-service": "load-balancer"}
    assert "create-server" in names  # server is a target in its own right


def test_bundle_interval_teardown(model):
    lc = compose(BUNDLE, model=model)
    names = _names(lc)
    del_subnet = names.index("delete-subnet")
    # subnet is deleted only after its LAST dependent (§2.5 rule 3)
    for dep in ("delete-port", "delete-load-balancer", "delete-server",
                "delete-vpc-endpoint", "delete-privatelink-service"):
        assert names.index(dep) < del_subnet, dep
    # vpc goes last of all
    assert names[-1] == "delete-vpc"
    assert names.index("delete-vpc-2") < len(names) - 1
    # igw (vpc-dependent) before vpc as well
    assert names.index("delete-igw") < names.index("delete-vpc")


def test_prerequisite_target_gets_verify_without_second_create(model):
    lc = compose(["vpc", "subnet", "port"], model=model)
    names = _names(lc)
    assert names.count("create-vpc") == 1
    assert names.count("create-subnet") == 1
    # verify grafted onto the shared instances (§2.5 rule 2)
    assert "verify-vpc" in names and "verify-subnet" in names
    assert names.index("verify-vpc") < names.index("create-subnet")
    # bundle verify steps carry the target's group tag
    assert _step(lc, "verify-port")["group"] == "port"
    assert lc["id"] == "bundle-port-subnet-vpc"


# ---------------------------------------------------------------------------
# peak quota

def test_peak_quota_bundle(model):
    p = plan(BUNDLE, model=model)
    assert p["peak_quota"] == {"lb": 1, "peering": 1, "security-group": 1,
                               "server": 1, "subnet": 1, "vpc": 2}


# ---------------------------------------------------------------------------
# determinism

def test_compose_is_deterministic(model):
    a = compose(BUNDLE, model=model)
    b = compose(BUNDLE, model=model)
    assert json.dumps(a) == json.dumps(b)
    # target order must not matter for a bundle
    c = compose(["port", "igw"], model=model)
    d = compose(["igw", "port"], model=model)
    assert json.dumps(c) == json.dumps(d)


# ---------------------------------------------------------------------------
# validator hook

def test_validator_hook_rejects_use_before_capture(model):
    broken = dict(model)
    broken["broken"] = {
        "service": "networking/vpc",
        "requires": [],
        "create": {"endpoint": "POST /v1/brokens",
                   # dot-less token: passes substitution untouched, then the
                   # validator hook flags it as used-before-captured
                   "body": {"ref": "{never_captured_var}"}},
        "capture": {"broken_id": "$.broken.id"},
        "delete": {"endpoint": "DELETE /v1/brokens/{broken_id}",
                   "destructive": True},
    }
    with pytest.raises(ComposeError, match="undefined placeholders"):
        compose(["broken"], model=broken)


def test_composed_lifecycles_satisfy_scenario_validator_shape(model):
    # the emitted dict only uses keys the on-disk validator knows about
    from regression.scenarios.validate import LIFECYCLE_KEYS, STEP_KEYS
    lc = compose(BUNDLE, model=model)
    assert set(lc) <= LIFECYCLE_KEYS
    for s in lc["steps"]:
        assert set(s) <= STEP_KEYS, s["name"]


# ---------------------------------------------------------------------------
# load_model

def test_load_model_merges_files_and_rejects_duplicates(tmp_path, model):
    a = {"version": 1, "resources": {"vpc": model["vpc"],
                                     "subnet": model["subnet"]}}
    b = {"version": 1, "resources": {"port": model["port"]}}
    (tmp_path / "networking__vpc.yaml").write_text(yaml.safe_dump(a))
    (tmp_path / "networking__port.yaml").write_text(yaml.safe_dump(b))
    (tmp_path / "_groups.yaml").write_text(
        yaml.safe_dump({"groups": {"nw-vpc": {"label": "network"}}}))
    m = composer.load_model(dir=str(tmp_path))
    assert set(m) == {"vpc", "subnet", "port"}
    # a composed lifecycle from the loaded model still validates
    lc = compose(["port"], model=m)
    assert lc["id"] == "gen-port"

    (tmp_path / "networking__dup.yaml").write_text(
        yaml.safe_dump({"resources": {"vpc": model["vpc"]}}))
    with pytest.raises(ComposeError, match="duplicate resource node"):
        composer.load_model(dir=str(tmp_path))


REAL_MODEL_DIR = Path(__file__).resolve().parents[2] / \
    "knowledge" / "formal" / "resources"


@pytest.mark.skipif(not REAL_MODEL_DIR.is_dir(),
                    reason="R1 has not landed knowledge/formal/resources yet")
def test_load_model_parses_real_resources():
    m = composer.load_model(dir=str(REAL_MODEL_DIR))
    assert m, "real resource model directory exists but is empty"
    for node_id, task in m.items():
        assert isinstance(task, dict), node_id
