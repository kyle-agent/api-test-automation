# READ-REACHABILITY — id-bound GET reachability from the resource model

> Generated: **2026-06-18** by `python -m spec.read_reachability` (Piece 2 of the create→조회(show) coverage effort). Pure static catalog×model join — no network, no engine, no live model.
>
> Cross-ref: `docs/COVERAGE-GETID-PLAN.md` §7 (probe_reads UNDER-SEEDING — the create→조회 gap) and its Piece 1 (engine auto-probe), Piece 2 (this report), Piece 3 (burn down model-gaps). The **model-gap** section below is Piece 3's worklist.

## Summary

Total id-bound GETs analyzed (services present in the model): **302**

| verdict | count | meaning |
|---|---|---|
| `model-gap` | 48 | a path-param has NO producing node — Piece 3 backlog |
| `query-param` | 17 | path-params produced but a required query param blocks auto-probe |
| `cat2-needs-child` | 6 | produced via a child beyond the resource's own create spine |
| `cat1-auto` | 231 | auto-probe (Piece 1) fires it for free |

## model-gap worklist (Piece 3)

Every id-bound GET with at least one path-param no model node captures. The `∅` param is the one to close (new capture / child node / list-recover sub-step). Near-miss column flags likely catalog↔model param NAME mismatches.

| service | GET path | unproduced param(s) | near-miss model capture(s) |
|---|---|---|---|
| apigateway | `/v1/apis/{api_id}/auths/{auth_id}` | `auth_id` | — |
| apigateway | `/v1/apis/{api_id}/stages/{stage_name}` | `stage_name` | — |
| baremetal | `/v1/baremetals/{baremetal_id}` | `baremetal_id` | — |
| cachestore | `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `engine_version_id` |
| cdn | `/v1/cdns/{id}` | `id` | — |
| certificatemanager | `/v1/certificatemanager/{certificate_id}` | `certificate_id` | `cert_id` |
| cloudcontrol | `/v1/guardrails/{guardrail_id}` | `guardrail_id` | — |
| data-flow | `/v1/data-flow-services/{data_flow_service_name}/check-duplication` | `data_flow_service_name` | — |
| data-flow | `/v1/data-flows/{data_flow_name}/check-duplication` | `data_flow_name` | — |
| data-ops | `/v1/data-ops-services/{data_ops_service_name}/check-duplication` | `data_ops_service_name` | — |
| data-ops | `/v1/data-ops/{data_ops_name}/check-duplication` | `data_ops_name` | — |
| dns | `/v1/hosted-zones/{hosted_zone_id}/records/{record_id}` | `record_id` | — |
| dns | `/v1/public-domain-names/{public_domain_id}` | `public_domain_id` | `publicip_id` |
| epas | `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `engine_version_id` |
| eventstreams | `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `engine_version_id` |
| iam | `/v1/resource-policies/{srn}` | `srn` | `rg_srn` |
| iam | `/v1/service-accounts/{service_account_id}` | `service_account_id` | `account_id` |
| loadbalancer | `/v1/loadbalancers/certificates/{lb_certificate_id}` | `lb_certificate_id` | — |
| loggingaudit | `/v1/logs/{logging_id}` | `logging_id` | `log_group_id`, `log_stream_id` |
| mariadb | `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `engine_version_id` |
| mysql | `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `engine_version_id` |
| postgresql | `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `engine_version_id` |
| quick-query | `/v1/quick-query/{quick_query_name}/check-duplication` | `quick_query_name` | — |
| resourcemanager | `/v1/resource-groups/{resource_group_id}` | `resource_group_id` | `group_id` |
| resourcemanager | `/v1/resource-groups/{resource_group_id}/resources` | `resource_group_id` | `group_id` |
| resourcemanager | `/v1/resources/{region}/{service}/{resource_type}/{resource_identifier}` | `region`, `service`, `resource_type`, `resource_identifier` | — |
| resourcemanager | `/v1/resources/{srn}` | `srn` | `rg_srn` |
| resourcemanager | `/v1/tags/{region}/{service}/{resource_type}/{resource_identifier}` | `region`, `service`, `resource_type`, `resource_identifier` | — |
| resourcemanager | `/v1/tags/{region}/{service}/{resource_type}/{resource_identifier}/{key}` | `region`, `service`, `resource_type`, `resource_identifier`, `key` | — |
| resourcemanager | `/v1/tags/{srn}` | `srn` | `rg_srn` |
| resourcemanager | `/v1/tags/{srn}/{key}` | `srn`, `key` | `rg_srn` |
| scr | `/v1/container-registries/{registry_id}` | `registry_id` | `reg_id` |
| scr | `/v1/container-registries/{registry_id}/repositories` | `registry_id` | `reg_id` |
| scr | `/v1/repositories/{repository_id}` | `repository_id` | `repo_id` |
| scr | `/v1/repositories/{repository_id}/images` | `repository_id` | `repo_id` |
| scr | `/v1/tagses/{tags_id}` | `tags_id` | — |
| scr | `/v1/tagses/{tags_id}/download/manifest` | `tags_id` | — |
| scr | `/v1/tagses/{tags_id}/packages` | `tags_id` | — |
| scr | `/v1/tagses/{tags_id}/secrets` | `tags_id` | — |
| scr | `/v1/tagses/{tags_id}/vulnerabilities` | `tags_id` | — |
| searchengine | `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `engine_version_id` |
| security-group | `/v1/security-group-rules/{security_group_rule_id}` | `security_group_rule_id` | `rule_id` |
| security-group | `/v1/security-groups/{security_group_id}` | `security_group_id` | `group_id` |
| servicewatch | `/v1/alerts/{id}` | `id` | — |
| sqlserver | `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `engine_version_id` |
| vertica | `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `engine_version_id` |
| virtualserver | `/v1/keypairs/{keypair_name}` | `keypair_name` | — |
| vpc | `/v1/subnets/{subnet_id}/vips/{vip_id}` | `vip_id` | — |

**Unproduced path-params by frequency** (a single capture/lookup node may close several rows):

| param | # GETs blocked | near-miss model capture(s) |
|---|---|---|
| `dbaas_engine_version_id` | 9 | `engine_version_id` |
| `tags_id` | 5 | — |
| `srn` | 4 | `rg_srn` |
| `region` | 3 | — |
| `resource_identifier` | 3 | — |
| `resource_type` | 3 | — |
| `service` | 3 | — |
| `id` | 2 | — |
| `key` | 2 | — |
| `registry_id` | 2 | `reg_id` |
| `repository_id` | 2 | `repo_id` |
| `resource_group_id` | 2 | `group_id` |
| `auth_id` | 1 | — |
| `baremetal_id` | 1 | — |
| `certificate_id` | 1 | `cert_id` |
| `data_flow_name` | 1 | — |
| `data_flow_service_name` | 1 | — |
| `data_ops_name` | 1 | — |
| `data_ops_service_name` | 1 | — |
| `guardrail_id` | 1 | — |
| `keypair_name` | 1 | — |
| `lb_certificate_id` | 1 | — |
| `logging_id` | 1 | `log_group_id`, `log_stream_id` |
| `public_domain_id` | 1 | `publicip_id` |
| `quick_query_name` | 1 | — |
| `record_id` | 1 | — |
| `security_group_id` | 1 | `group_id` |
| `security_group_rule_id` | 1 | `rule_id` |
| `service_account_id` | 1 | `account_id` |
| `stage_name` | 1 | — |
| `vip_id` | 1 | — |

## Per-service breakdown

Services sorted by `model-gap` count (descending).

### scr  (12 id-bound GET — model-gap=9 · cat1-auto=3)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/container-registries/{registry_id}` | `registry_id` | `registry_id`→∅ | no | `model-gap` |
| `/v1/container-registries/{registry_id}/repositories` | `registry_id` | `registry_id`→∅ | no | `model-gap` |
| `/v1/images/{image_id}` | `image_id` | `image_id`→cloudml-image,gpu-node-image,image,image-registration | no | `cat1-auto` |
| `/v1/images/{image_id}/lifecycle-policy/preview` | `image_id` | `image_id`→cloudml-image,gpu-node-image,image,image-registration | no | `cat1-auto` |
| `/v1/images/{image_id}/tagses` | `image_id` | `image_id`→cloudml-image,gpu-node-image,image,image-registration | no | `cat1-auto` |
| `/v1/repositories/{repository_id}` | `repository_id` | `repository_id`→∅ | no | `model-gap` |
| `/v1/repositories/{repository_id}/images` | `repository_id` | `repository_id`→∅ | no | `model-gap` |
| `/v1/tagses/{tags_id}` | `tags_id` | `tags_id`→∅ | no | `model-gap` |
| `/v1/tagses/{tags_id}/download/manifest` | `tags_id` | `tags_id`→∅ | no | `model-gap` |
| `/v1/tagses/{tags_id}/packages` | `tags_id` | `tags_id`→∅ | no | `model-gap` |
| `/v1/tagses/{tags_id}/secrets` | `tags_id` | `tags_id`→∅ | no | `model-gap` |
| `/v1/tagses/{tags_id}/vulnerabilities` | `tags_id` | `tags_id`→∅ | no | `model-gap` |

### resourcemanager  (8 id-bound GET — model-gap=8)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/resource-groups/{resource_group_id}` | `resource_group_id` | `resource_group_id`→∅ | no | `model-gap` |
| `/v1/resource-groups/{resource_group_id}/resources` | `resource_group_id` | `resource_group_id`→∅ | no | `model-gap` |
| `/v1/resources/{region}/{service}/{resource_type}/{resource_identifier}` | `region`, `service`, `resource_type`, `resource_identifier` | `region`→∅<br>`service`→∅<br>`resource_type`→∅<br>`resource_identifier`→∅ | no | `model-gap` |
| `/v1/resources/{srn}` | `srn` | `srn`→∅ | no | `model-gap` |
| `/v1/tags/{region}/{service}/{resource_type}/{resource_identifier}` | `region`, `service`, `resource_type`, `resource_identifier` | `region`→∅<br>`service`→∅<br>`resource_type`→∅<br>`resource_identifier`→∅ | no | `model-gap` |
| `/v1/tags/{region}/{service}/{resource_type}/{resource_identifier}/{key}` | `region`, `service`, `resource_type`, `resource_identifier`, `key` | `region`→∅<br>`service`→∅<br>`resource_type`→∅<br>`resource_identifier`→∅<br>`key`→∅ | no | `model-gap` |
| `/v1/tags/{srn}` | `srn` | `srn`→∅ | no | `model-gap` |
| `/v1/tags/{srn}/{key}` | `srn`, `key` | `srn`→∅<br>`key`→∅ | no | `model-gap` |

### apigateway  (19 id-bound GET — model-gap=2 · query-param=1 · cat2-needs-child=1 · cat1-auto=15)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/apis/{api_id}` | `api_id` | `api_id`→apigw-api | no | `cat1-auto` |
| `/v1/apis/{api_id}/access-controls` | `api_id` | `api_id`→apigw-api | no | `cat1-auto` |
| `/v1/apis/{api_id}/access-controls/{access_control_id}` | `api_id`, `access_control_id` | `api_id`→apigw-api<br>`access_control_id`→apigw-access-control | no | `cat1-auto` |
| `/v1/apis/{api_id}/auths` | `api_id` | `api_id`→apigw-api | no | `cat1-auto` |
| `/v1/apis/{api_id}/auths/{auth_id}` | `api_id`, `auth_id` | `api_id`→apigw-api<br>`auth_id`→∅ | no | `model-gap` |
| `/v1/apis/{api_id}/connected-endpoints` | `api_id` | `api_id`→apigw-api | no | `cat1-auto` |
| `/v1/apis/{api_id}/deployments` | `api_id` | `api_id`→apigw-api | no | `cat1-auto` |
| `/v1/apis/{api_id}/reports` | `api_id` | `api_id`→apigw-api | **yes**: stage_name, start_date, end_date | `query-param` |
| `/v1/apis/{api_id}/resource-policies` | `api_id` | `api_id`→apigw-api | no | `cat1-auto` |
| `/v1/apis/{api_id}/resources` | `api_id` | `api_id`→apigw-api | no | `cat1-auto` |
| `/v1/apis/{api_id}/resources/{resource_id}` | `api_id`, `resource_id` | `api_id`→apigw-api<br>`resource_id`→apigw-resource | no | `cat1-auto` |
| `/v1/apis/{api_id}/resources/{resource_id}/methods` | `api_id`, `resource_id` | `api_id`→apigw-api<br>`resource_id`→apigw-resource | no | `cat1-auto` |
| `/v1/apis/{api_id}/resources/{resource_id}/methods/{method_type}` | `api_id`, `resource_id`, `method_type` | `api_id`→apigw-api<br>`resource_id`→apigw-resource<br>`method_type`→apigw-method | no | `cat2-needs-child` |
| `/v1/apis/{api_id}/stages` | `api_id` | `api_id`→apigw-api | no | `cat1-auto` |
| `/v1/apis/{api_id}/stages/{stage_name}` | `api_id`, `stage_name` | `api_id`→apigw-api<br>`stage_name`→∅ | no | `model-gap` |
| `/v1/apis/{api_id}/usage-plans` | `api_id` | `api_id`→apigw-api | no | `cat1-auto` |
| `/v1/apis/{api_id}/usage-plans/{usage_plan_id}` | `api_id`, `usage_plan_id` | `api_id`→apigw-api<br>`usage_plan_id`→apigw-usage-plan | no | `cat1-auto` |
| `/v1/apis/{api_id}/usage-plans/{usage_plan_id}/api-keys` | `api_id`, `usage_plan_id` | `api_id`→apigw-api<br>`usage_plan_id`→apigw-usage-plan | no | `cat1-auto` |
| `/v1/privatelink-endpoints/{privatelink_endpoint_id}` | `privatelink_endpoint_id` | `privatelink_endpoint_id`→apigw-privatelink-endpoint | no | `cat1-auto` |

### data-flow  (6 id-bound GET — model-gap=2 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/data-flow-services/data-flows/{data_flow_id}/sub-versions` | `data_flow_id` | `data_flow_id`→data-flow | no | `cat1-auto` |
| `/v1/data-flow-services/{data_flow_service_id}` | `data_flow_service_id` | `data_flow_service_id`→data-flow-service | no | `cat1-auto` |
| `/v1/data-flow-services/{data_flow_service_name}/check-duplication` | `data_flow_service_name` | `data_flow_service_name`→∅ | no | `model-gap` |
| `/v1/data-flows/clusters/{cluster_id}/ingress-controllers` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/data-flows/{data_flow_id}` | `data_flow_id` | `data_flow_id`→data-flow | no | `cat1-auto` |
| `/v1/data-flows/{data_flow_name}/check-duplication` | `data_flow_name` | `data_flow_name`→∅ | no | `model-gap` |

### data-ops  (6 id-bound GET — model-gap=2 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/data-ops-services/data-ops/{data_ops_id}/sub-versions` | `data_ops_id` | `data_ops_id`→data-ops | no | `cat1-auto` |
| `/v1/data-ops-services/{data_ops_service_id}` | `data_ops_service_id` | `data_ops_service_id`→data-ops-service | no | `cat1-auto` |
| `/v1/data-ops-services/{data_ops_service_name}/check-duplication` | `data_ops_service_name` | `data_ops_service_name`→∅ | no | `model-gap` |
| `/v1/data-ops/clusters/{cluster_id}/ingress-controllers` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/data-ops/{data_ops_id}` | `data_ops_id` | `data_ops_id`→data-ops | no | `cat1-auto` |
| `/v1/data-ops/{data_ops_name}/check-duplication` | `data_ops_name` | `data_ops_name`→∅ | no | `model-gap` |

### dns  (5 id-bound GET — model-gap=2 · cat1-auto=3)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/hosted-zones/{hosted_zone_id}` | `hosted_zone_id` | `hosted_zone_id`→hosted-zone | no | `cat1-auto` |
| `/v1/hosted-zones/{hosted_zone_id}/records` | `hosted_zone_id` | `hosted_zone_id`→hosted-zone | no | `cat1-auto` |
| `/v1/hosted-zones/{hosted_zone_id}/records/{record_id}` | `hosted_zone_id`, `record_id` | `hosted_zone_id`→hosted-zone<br>`record_id`→∅ | no | `model-gap` |
| `/v1/private-dns/{private_dns_id}` | `private_dns_id` | `private_dns_id`→private-dns | no | `cat1-auto` |
| `/v1/public-domain-names/{public_domain_id}` | `public_domain_id` | `public_domain_id`→∅ | no | `model-gap` |

### iam  (14 id-bound GET — model-gap=2 · cat2-needs-child=1 · cat1-auto=11)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/access-keys/{access_key_id}` | `access_key_id` | `access_key_id`→iam-access-key | no | `cat1-auto` |
| `/v1/accounts/{account_id}/users` | `account_id` | `account_id`→organization-account,sw-custom-log-collect | no | `cat1-auto` |
| `/v1/accounts/{account_id}/users/{user_id}` | `account_id`, `user_id` | `account_id`→organization-account,sw-custom-log-collect<br>`user_id`→iam-user | no | `cat2-needs-child` |
| `/v1/groups/{group_id}` | `group_id` | `group_id`→idc-group,iam-group | no | `cat1-auto` |
| `/v1/groups/{group_id}/members` | `group_id` | `group_id`→idc-group,iam-group | no | `cat1-auto` |
| `/v1/groups/{group_id}/policy-bindings` | `group_id` | `group_id`→idc-group,iam-group | no | `cat1-auto` |
| `/v1/policies/{policy_id}` | `policy_id` | `policy_id`→asg-policy,iam-policy | no | `cat1-auto` |
| `/v1/policies/{policy_id}/bindings` | `policy_id` | `policy_id`→asg-policy,iam-policy | no | `cat1-auto` |
| `/v1/resource-policies/{srn}` | `srn` | `srn`→∅ | no | `model-gap` |
| `/v1/roles/{role_id}` | `role_id` | `role_id`→iam-role | no | `cat1-auto` |
| `/v1/roles/{role_id}/policy-bindings` | `role_id` | `role_id`→iam-role | no | `cat1-auto` |
| `/v1/saml-providers/{saml_provider_id}` | `saml_provider_id` | `saml_provider_id`→iam-saml-provider | no | `cat1-auto` |
| `/v1/service-accounts/{service_account_id}` | `service_account_id` | `service_account_id`→∅ | no | `model-gap` |
| `/v1/users/{user_id}/policy-bindings` | `user_id` | `user_id`→iam-user | no | `cat1-auto` |

### security-group  (2 id-bound GET — model-gap=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/security-group-rules/{security_group_rule_id}` | `security_group_rule_id` | `security_group_rule_id`→∅ | no | `model-gap` |
| `/v1/security-groups/{security_group_id}` | `security_group_id` | `security_group_id`→∅ | no | `model-gap` |

### baremetal  (1 id-bound GET — model-gap=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/baremetals/{baremetal_id}` | `baremetal_id` | `baremetal_id`→∅ | no | `model-gap` |

### cachestore  (6 id-bound GET — model-gap=1 · cat1-auto=5)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/commands` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→∅ | no | `model-gap` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### cdn  (1 id-bound GET — model-gap=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cdns/{id}` | `id` | `id`→∅ | no | `model-gap` |

### certificatemanager  (1 id-bound GET — model-gap=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/certificatemanager/{certificate_id}` | `certificate_id` | `certificate_id`→∅ | no | `model-gap` |

### cloudcontrol  (2 id-bound GET — model-gap=1 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/guardrails/{guardrail_id}` | `guardrail_id` | `guardrail_id`→∅ | no | `model-gap` |
| `/v1/landing-zones/{landing_zone_id}` | `landing_zone_id` | `landing_zone_id`→cloudcontrol-landing-zone | no | `cat1-auto` |

### epas  (8 id-bound GET — model-gap=1 · cat1-auto=7)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/archive` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/replicas` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→∅ | no | `model-gap` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### eventstreams  (4 id-bound GET — model-gap=1 · cat1-auto=3)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→∅ | no | `model-gap` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### loadbalancer  (9 id-bound GET — model-gap=1 · cat1-auto=8)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/lb-health-checks/{lb_health_check_id}` | `lb_health_check_id` | `lb_health_check_id`→lb-health-check | no | `cat1-auto` |
| `/v1/lb-listeners/{listener_id}` | `listener_id` | `listener_id`→lb-listener | no | `cat1-auto` |
| `/v1/lb-server-groups/{lb_server_group_id}` | `lb_server_group_id` | `lb_server_group_id`→lb-server-group | no | `cat1-auto` |
| `/v1/lb-server-groups/{lb_server_group_id}/members` | `lb_server_group_id` | `lb_server_group_id`→lb-server-group | no | `cat1-auto` |
| `/v1/lb-server-groups/{lb_server_group_id}/members/{member_id}` | `lb_server_group_id`, `member_id` | `lb_server_group_id`→lb-server-group<br>`member_id`→lb-member | no | `cat1-auto` |
| `/v1/loadbalancers/certificates/{lb_certificate_id}` | `lb_certificate_id` | `lb_certificate_id`→∅ | no | `model-gap` |
| `/v1/loadbalancers/{loadbalancer_id}` | `loadbalancer_id` | `loadbalancer_id`→load-balancer | no | `cat1-auto` |
| `/v1/loadbalancers/{loadbalancer_id}/private-static-nats` | `loadbalancer_id` | `loadbalancer_id`→load-balancer | no | `cat1-auto` |
| `/v1/loadbalancers/{loadbalancer_id}/static-nats` | `loadbalancer_id` | `loadbalancer_id`→load-balancer | no | `cat1-auto` |

### loggingaudit  (2 id-bound GET — model-gap=1 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/logs/{logging_id}` | `logging_id` | `logging_id`→∅ | no | `model-gap` |
| `/v1/trails/{trail_id}` | `trail_id` | `trail_id`→trail | no | `cat1-auto` |

### mariadb  (8 id-bound GET — model-gap=1 · cat1-auto=7)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/archive` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/replicas` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→∅ | no | `model-gap` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### mysql  (8 id-bound GET — model-gap=1 · cat1-auto=7)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/archive` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/replicas` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→∅ | no | `model-gap` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### postgresql  (8 id-bound GET — model-gap=1 · cat1-auto=7)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/archive` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/replicas` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→∅ | no | `model-gap` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### quick-query  (2 id-bound GET — model-gap=1 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/quick-query/{quick_query_id}` | `quick_query_id` | `quick_query_id`→quick-query | no | `cat1-auto` |
| `/v1/quick-query/{quick_query_name}/check-duplication` | `quick_query_name` | `quick_query_name`→∅ | no | `model-gap` |

### searchengine  (4 id-bound GET — model-gap=1 · cat1-auto=3)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→∅ | no | `model-gap` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### servicewatch  (5 id-bound GET — model-gap=1 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/alerts/{id}` | `id` | `id`→∅ | no | `model-gap` |
| `/v1/dashboards/{dashboard_id}` | `dashboard_id` | `dashboard_id`→dashboard | no | `cat1-auto` |
| `/v1/event-rules/{event_rule_id}` | `event_rule_id` | `event_rule_id`→event-rule | no | `cat1-auto` |
| `/v1/log-groups/{log_group_id}` | `log_group_id` | `log_group_id`→log-group | no | `cat1-auto` |
| `/v1/log-groups/{log_group_id}/log-streams/{log_stream_id}` | `log_group_id`, `log_stream_id` | `log_group_id`→log-group<br>`log_stream_id`→log-stream | no | `cat1-auto` |

### sqlserver  (6 id-bound GET — model-gap=1 · cat1-auto=5)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→∅ | no | `model-gap` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### vertica  (4 id-bound GET — model-gap=1 · cat1-auto=3)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→∅ | no | `model-gap` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### virtualserver  (29 id-bound GET — model-gap=1 · cat2-needs-child=4 · cat1-auto=24)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/auto-scaling-groups/{auto_scaling_group_id}` | `auto_scaling_group_id` | `auto_scaling_group_id`→auto-scaling-group | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/lb-server-groups` | `auto_scaling_group_id` | `auto_scaling_group_id`→auto-scaling-group | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/notifications` | `auto_scaling_group_id` | `auto_scaling_group_id`→auto-scaling-group | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/notifications/{notification_id}` | `auto_scaling_group_id`, `notification_id` | `auto_scaling_group_id`→auto-scaling-group<br>`notification_id`→asg-notification | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/policies` | `auto_scaling_group_id` | `auto_scaling_group_id`→auto-scaling-group | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/policies/{policy_id}` | `auto_scaling_group_id`, `policy_id` | `auto_scaling_group_id`→auto-scaling-group<br>`policy_id`→asg-policy,iam-policy | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/schedules` | `auto_scaling_group_id` | `auto_scaling_group_id`→auto-scaling-group | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/schedules/{schedule_id}` | `auto_scaling_group_id`, `schedule_id` | `auto_scaling_group_id`→auto-scaling-group<br>`schedule_id`→asg-schedule | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/virtual-servers` | `auto_scaling_group_id` | `auto_scaling_group_id`→auto-scaling-group | no | `cat1-auto` |
| `/v1/images/{image_id}` | `image_id` | `image_id`→cloudml-image,gpu-node-image,image,image-registration | no | `cat1-auto` |
| `/v1/images/{image_id}/members` | `image_id` | `image_id`→cloudml-image,gpu-node-image,image,image-registration | no | `cat1-auto` |
| `/v1/images/{image_id}/members/{member_id}` | `image_id`, `member_id` | `image_id`→cloudml-image,gpu-node-image,image,image-registration<br>`member_id`→lb-member | no | `cat2-needs-child` |
| `/v1/keypairs/{keypair_name}` | `keypair_name` | `keypair_name`→∅ | no | `model-gap` |
| `/v1/launch-configurations/{launch_configuration_id}` | `launch_configuration_id` | `launch_configuration_id`→launch-configuration | no | `cat1-auto` |
| `/v1/server-groups/{server_group_id}` | `server_group_id` | `server_group_id`→server-group | no | `cat1-auto` |
| `/v1/server-types/{server_type_id}` | `server_type_id` | `server_type_id`→gpu-node-product,server-type | no | `cat1-auto` |
| `/v1/servers/{server_id}` | `server_id` | `server_id`→server | no | `cat1-auto` |
| `/v1/servers/{server_id}/console-log` | `server_id` | `server_id`→server | no | `cat1-auto` |
| `/v1/servers/{server_id}/interfaces` | `server_id` | `server_id`→server | no | `cat1-auto` |
| `/v1/servers/{server_id}/interfaces/{port_id}` | `server_id`, `port_id` | `server_id`→server<br>`port_id`→port | no | `cat2-needs-child` |
| `/v1/servers/{server_id}/ips` | `server_id` | `server_id`→server | no | `cat1-auto` |
| `/v1/servers/{server_id}/ips/{subnet_id}` | `server_id`, `subnet_id` | `server_id`→server<br>`subnet_id`→subnet,endpoint-subnet | no | `cat2-needs-child` |
| `/v1/servers/{server_id}/security-groups` | `server_id` | `server_id`→server | no | `cat1-auto` |
| `/v1/servers/{server_id}/volumes` | `server_id` | `server_id`→server | no | `cat1-auto` |
| `/v1/servers/{server_id}/volumes/{volume_id}` | `server_id`, `volume_id` | `server_id`→server<br>`volume_id`→bm-block-volume,filestorage-volume,pfs-volume | no | `cat2-needs-child` |
| `/v1/snapshots/{snapshot_id}` | `snapshot_id` | `snapshot_id`→bm-volume-snapshot,fs-snapshot,pfs-snapshot | no | `cat1-auto` |
| `/v1/volume-transfer/{transfer_id}` | `transfer_id` | `transfer_id`→volume-transfer | no | `cat1-auto` |
| `/v1/volume-types/{volume_type_id}` | `volume_type_id` | `volume_type_id`→volume-type | no | `cat1-auto` |
| `/v1/volumes/{volume_id}` | `volume_id` | `volume_id`→bm-block-volume,filestorage-volume,pfs-volume | no | `cat1-auto` |

### vpc  (20 id-bound GET — model-gap=1 · cat1-auto=19)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/internet-gateways/{internet_gateway_id}` | `internet_gateway_id` | `internet_gateway_id`→internet-gateway | no | `cat1-auto` |
| `/v1/nat-gateways/{nat_gateway_id}` | `nat_gateway_id` | `nat_gateway_id`→nat-gateway | no | `cat1-auto` |
| `/v1/ports/{port_id}` | `port_id` | `port_id`→port | no | `cat1-auto` |
| `/v1/private-nats/{private_nat_id}` | `private_nat_id` | `private_nat_id`→private-nat | no | `cat1-auto` |
| `/v1/private-nats/{private_nat_id}/private-nat-ips` | `private_nat_id` | `private_nat_id`→private-nat | no | `cat1-auto` |
| `/v1/privatelink-endpoints/{privatelink_endpoint_id}` | `privatelink_endpoint_id` | `privatelink_endpoint_id`→apigw-privatelink-endpoint | no | `cat1-auto` |
| `/v1/privatelink-services/{privatelink_service_id}` | `privatelink_service_id` | `privatelink_service_id`→privatelink-service | no | `cat1-auto` |
| `/v1/privatelink-services/{privatelink_service_id}/connected-endpoints` | `privatelink_service_id` | `privatelink_service_id`→privatelink-service | no | `cat1-auto` |
| `/v1/publicips/{publicip_id}` | `publicip_id` | `publicip_id`→public-ip | no | `cat1-auto` |
| `/v1/subnets/{subnet_id}` | `subnet_id` | `subnet_id`→subnet,endpoint-subnet | no | `cat1-auto` |
| `/v1/subnets/{subnet_id}/sap-secondary-subnets` | `subnet_id` | `subnet_id`→subnet,endpoint-subnet | no | `cat1-auto` |
| `/v1/subnets/{subnet_id}/vips` | `subnet_id` | `subnet_id`→subnet,endpoint-subnet | no | `cat1-auto` |
| `/v1/subnets/{subnet_id}/vips/{vip_id}` | `subnet_id`, `vip_id` | `subnet_id`→subnet,endpoint-subnet<br>`vip_id`→∅ | no | `model-gap` |
| `/v1/transit-gateways/{transit_gateway_id}` | `transit_gateway_id` | `transit_gateway_id`→transit-gateway,tgw-vpc-connection | no | `cat1-auto` |
| `/v1/transit-gateways/{transit_gateway_id}/routing-rules` | `transit_gateway_id` | `transit_gateway_id`→transit-gateway,tgw-vpc-connection | no | `cat1-auto` |
| `/v1/transit-gateways/{transit_gateway_id}/vpc-connections` | `transit_gateway_id` | `transit_gateway_id`→transit-gateway,tgw-vpc-connection | no | `cat1-auto` |
| `/v1/vpc-endpoints/{vpc_endpoint_id}` | `vpc_endpoint_id` | `vpc_endpoint_id`→vpc-endpoint | no | `cat1-auto` |
| `/v1/vpc-peerings/{vpc_peering_id}` | `vpc_peering_id` | `vpc_peering_id`→vpc-peering | no | `cat1-auto` |
| `/v1/vpc-peerings/{vpc_peering_id}/routing-rules` | `vpc_peering_id` | `vpc_peering_id`→vpc-peering | no | `cat1-auto` |
| `/v1/vpcs/{vpc_id}` | `vpc_id` | `vpc_id`→vpc | no | `cat1-auto` |

### aimlops-platform  (6 id-bound GET — query-param=2 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/aimlops-platform/clusters/{cluster_id}/check-version` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | **yes**: version | `query-param` |
| `/v1/aimlops-platform/clusters/{cluster_id}/validate-namespaces` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/aimlops-platform/clusters/{cluster_id}/validate-resources` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | **yes**: product_type | `query-param` |
| `/v1/aimlops-platform/internal/clusters/{cluster_id}/nodes` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/aimlops-platform/internal/clusters/{cluster_id}/storageclasses` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/aimlops-platform/{release_id}` | `release_id` | `release_id`→aimlops-platform | no | `cat1-auto` |

### archivestorage  (6 id-bound GET — query-param=2 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/archiving-policies/{archiving_policy_id}` | `archiving_policy_id` | `archiving_policy_id`→archiving-policy | **yes**: bucket_id | `query-param` |
| `/v1/buckets/{bucket_id}` | `bucket_id` | `bucket_id`→archive-bucket | no | `cat1-auto` |
| `/v1/buckets/{bucket_id}/encryption` | `bucket_id` | `bucket_id`→archive-bucket | no | `cat1-auto` |
| `/v1/buckets/{bucket_id}/object-versions` | `bucket_id` | `bucket_id`→archive-bucket | **yes**: object_path | `query-param` |
| `/v1/buckets/{bucket_id}/objects` | `bucket_id` | `bucket_id`→archive-bucket | no | `cat1-auto` |
| `/v1/buckets/{bucket_id}/versioning` | `bucket_id` | `bucket_id`→archive-bucket | no | `cat1-auto` |

### backup  (10 id-bound GET — query-param=2 · cat1-auto=8)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/backup-agents/{backup_agent_id}` | `backup_agent_id` | `backup_agent_id`→backup-agent | no | `cat1-auto` |
| `/v1/backup-agents/{backup_agent_id}/check-connection-state` | `backup_agent_id` | `backup_agent_id`→backup-agent | no | `cat1-auto` |
| `/v1/backups/{backup_id}` | `backup_id` | `backup_id`→backup-policy | no | `cat1-auto` |
| `/v1/backups/{backup_id}/agent-backup-restore-targets` | `backup_id` | `backup_id`→backup-policy | no | `cat1-auto` |
| `/v1/backups/{backup_id}/backup-histories` | `backup_id` | `backup_id`→backup-policy | no | `cat1-auto` |
| `/v1/backups/{backup_id}/filesystem-path` | `backup_id` | `backup_id`→backup-policy | **yes**: restore_target_id, path | `query-param` |
| `/v1/backups/{backup_id}/restore-histories` | `backup_id` | `backup_id`→backup-policy | no | `cat1-auto` |
| `/v1/backups/{backup_id}/restore-targets` | `backup_id` | `backup_id`→backup-policy | no | `cat1-auto` |
| `/v1/backups/{backup_id}/restore/restorable-subnets` | `backup_id` | `backup_id`→backup-policy | **yes**: region | `query-param` |
| `/v1/backups/{backup_id}/schedules` | `backup_id` | `backup_id`→backup-policy | no | `cat1-auto` |

### baremetal-blockstorage  (6 id-bound GET — cat1-auto=6)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/volume-groups/{volume_group_id}` | `volume_group_id` | `volume_group_id`→bm-volume-group | no | `cat1-auto` |
| `/v1/volume-groups/{volume_group_id}/replications` | `volume_group_id` | `volume_group_id`→bm-volume-group | no | `cat1-auto` |
| `/v1/volume-groups/{volume_group_id}/snapshots` | `volume_group_id` | `volume_group_id`→bm-volume-group | no | `cat1-auto` |
| `/v1/volumes/{volume_id}` | `volume_id` | `volume_id`→bm-block-volume,filestorage-volume,pfs-volume | no | `cat1-auto` |
| `/v1/volumes/{volume_id}/replications` | `volume_id` | `volume_id`→bm-block-volume,filestorage-volume,pfs-volume | no | `cat1-auto` |
| `/v1/volumes/{volume_id}/snapshots` | `volume_id` | `volume_id`→bm-block-volume,filestorage-volume,pfs-volume | no | `cat1-auto` |

### billingplan  (1 id-bound GET — cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/planned-computes/{planned_compute_id}` | `planned_compute_id` | `planned_compute_id`→planned-compute | no | `cat1-auto` |

### budget  (1 id-bound GET — cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/budgets/account/{budget_id}` | `budget_id` | `budget_id`→account-budget | no | `cat1-auto` |

### cloud-ml  (4 id-bound GET — cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cloud-ml/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/cloud-ml/clusters/{cluster_id}/check-releasable` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/cloud-ml/clusters/{cluster_id}/estimate` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/cloud-ml/{cloud_ml_id}` | `cloud_ml_id` | `cloud_ml_id`→cloud-ml | no | `cat1-auto` |

### cloudmonitoring  (6 id-bound GET — query-param=1 · cat1-auto=5)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}` | `eventPolicyId` | `eventPolicyId`→cm-event-policy | no | `cat1-auto` |
| `/v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}/histories` | `eventPolicyId` | `eventPolicyId`→cm-event-policy | **yes**: queryStartDt, queryEndDt | `query-param` |
| `/v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}/notifications` | `eventPolicyId` | `eventPolicyId`→cm-event-policy | no | `cat1-auto` |
| `/v1/cloudmonitorings/event/v2/events/{eventId}` | `eventId` | `eventId`→cm-event | no | `cat1-auto` |
| `/v1/cloudmonitorings/event/v2/events/{eventId}/notification-states` | `eventId` | `eventId`→cm-event | no | `cat1-auto` |
| `/v1/cloudmonitorings/product/v2/addrbooks/{addrbookId}/members` | `addrbookId` | `addrbookId`→cm-addrbook | no | `cat1-auto` |

### configinspection  (1 id-bound GET — cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/configinspection/diagnosis/detail/{diagnosis_id}` | `diagnosis_id` | `diagnosis_id`→diagnosis | no | `cat1-auto` |

### devopsservice  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/devops-services/{devops_service_id}` | `devops_service_id` | `devops_service_id`→devops-service | no | `cat1-auto` |
| `/v1/devops-services/{devops_service_id}/check-deletable` | `devops_service_id` | `devops_service_id`→devops-service | no | `cat1-auto` |

### direct-connect  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/direct-connects/{direct_connect_id}` | `direct_connect_id` | `direct_connect_id`→direct-connect | no | `cat1-auto` |
| `/v1/direct-connects/{direct_connect_id}/routing-rules` | `direct_connect_id` | `direct_connect_id`→direct-connect | no | `cat1-auto` |

### filestorage  (3 id-bound GET — query-param=1 · cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/replications/{replication_id}` | `replication_id` | `replication_id`→fs-replication | **yes**: volume_id | `query-param` |
| `/v1/volumes/{volume_id}` | `volume_id` | `volume_id`→bm-block-volume,filestorage-volume,pfs-volume | no | `cat1-auto` |
| `/v1/volumes/{volume_id}/access-rules` | `volume_id` | `volume_id`→bm-block-volume,filestorage-volume,pfs-volume | no | `cat1-auto` |

### firewall  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/firewalls/rules/{firewall_rule_id}` | `firewall_rule_id` | `firewall_rule_id`→firewall-rule | no | `cat1-auto` |
| `/v1/firewalls/{firewall_id}` | `firewall_id` | `firewall_id`→firewall | no | `cat1-auto` |

### gslb  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/gslbs/{gslb_id}` | `gslb_id` | `gslb_id`→gslb | no | `cat1-auto` |
| `/v1/gslbs/{gslb_id}/resources` | `gslb_id` | `gslb_id`→gslb | no | `cat1-auto` |

### iam-identity-center  (6 id-bound GET — query-param=5 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/groups/{group_id}` | `group_id` | `group_id`→idc-group,iam-group | **yes**: instance_id | `query-param` |
| `/v1/groups/{group_id}/users` | `group_id` | `group_id`→idc-group,iam-group | **yes**: instance_id | `query-param` |
| `/v1/instances/{instance_id}` | `instance_id` | `instance_id`→iam-identity-center | no | `cat1-auto` |
| `/v1/permission-sets/{permission_set_id}` | `permission_set_id` | `permission_set_id`→idc-permission-set | **yes**: instance_id | `query-param` |
| `/v1/permission-sets/{permission_set_id}/policies` | `permission_set_id` | `permission_set_id`→idc-permission-set | **yes**: instance_id | `query-param` |
| `/v1/users/{user_uuid}` | `user_uuid` | `user_uuid`→idc-user | **yes**: instance_id | `query-param` |

### kms  (3 id-bound GET — cat1-auto=3)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/kms/transit/{key_id}` | `key_id` | `key_id`→kms-key | no | `cat1-auto` |
| `/v1/kms/transit/{key_id}/users` | `key_id` | `key_id`→kms-key | no | `cat1-auto` |
| `/v1/managed-kms/transit/{key_id}` | `key_id` | `key_id`→kms-key | no | `cat1-auto` |

### multinodegpucluster  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cluster-fabrics/{cluster_fabric_id}` | `cluster_fabric_id` | `cluster_fabric_id`→gpu-node-fabric | no | `cat1-auto` |
| `/v1/gpu-nodes/{gpu_node_id}` | `gpu_node_id` | `gpu_node_id`→gpu-node | no | `cat1-auto` |

### organization  (5 id-bound GET — cat1-auto=5)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/organization-accounts/{account_id}` | `account_id` | `account_id`→organization-account,sw-custom-log-collect | no | `cat1-auto` |
| `/v1/organization-units/{unit_id}` | `unit_id` | `unit_id`→organization-unit | no | `cat1-auto` |
| `/v1/organization-units/{unit_id}/parents` | `unit_id` | `unit_id`→organization-unit | no | `cat1-auto` |
| `/v1/organizations/{organization_id}` | `organization_id` | `organization_id`→organization | no | `cat1-auto` |
| `/v1/service-control-policies/{policy_id}` | `policy_id` | `policy_id`→asg-policy,iam-policy | no | `cat1-auto` |

### parallel-filestorage  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/volumes/{volume_id}` | `volume_id` | `volume_id`→bm-block-volume,filestorage-volume,pfs-volume | no | `cat1-auto` |
| `/v1/volumes/{volume_id}/access-rules` | `volume_id` | `volume_id`→bm-block-volume,filestorage-volume,pfs-volume | no | `cat1-auto` |

### product  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/product-categories/{category_id}` | `category_id` | `category_id`→product-category | no | `cat1-auto` |
| `/v1/products/{product_id}` | `product_id` | `product_id`→product | no | `cat1-auto` |

### queueservice  (2 id-bound GET — query-param=1 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/queues/{queue_id}` | `queue_id` | `queue_id`→queue,queue-fifo | no | `cat1-auto` |
| `/v1/queues/{queue_id}/attributes` | `queue_id` | `queue_id`→queue,queue-fifo | **yes**: attributes, name | `query-param` |

### quota  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/account-quotas/{account_quota_id}` | `account_quota_id` | `account_quota_id`→account-quota | no | `cat1-auto` |
| `/v1/quota-requests/{request_id}` | `request_id` | `request_id`→quota-request | no | `cat1-auto` |

### scf  (12 id-bound GET — cat1-auto=12)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cloud-functions/{cloud_function_id}` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/codes` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/config` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/environment-variables` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/privatelink-endpoints` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/privatelink-services` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/resource-policies` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/url` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/logs` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/metrics` | `cloud_function_id` | `cloud_function_id`→scf-function | no | `cat1-auto` |
| `/v1/triggers/{trigger_id}` | `trigger_id` | `trigger_id`→scf-cronjob-trigger,scf-apigateway-trigger | no | `cat1-auto` |

### secretsmanager  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/secrets/{secret_id}` | `secret_id` | `secret_id`→secret | no | `cat1-auto` |
| `/v1/secrets/{secret_id}/versions` | `secret_id` | `secret_id`→secret | no | `cat1-auto` |

### secretvault  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/secretvault/{secret_vault_id}` | `secret_vault_id` | `secret_vault_id`→secretvault-vault | no | `cat1-auto` |
| `/v1/temporarykey/{secret_vault_id}` | `secret_vault_id` | `secret_vault_id`→secretvault-vault | no | `cat1-auto` |

### ske  (6 id-bound GET — query-param=2 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/kubeconfig` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | **yes**: kubeconfig_type | `query-param` |
| `/v1/clusters/{cluster_id}/nodepools` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/user-kubeconfig` | `cluster_id` | `cluster_id`→ske-cluster,eventstreams-cluster,searchengine-cluster,vertica-cluster,cachestore-cluster,epas-cluster,mariadb-cluster,mysql-cluster,postgresql-cluster,sqlserver-cluster | **yes**: kubeconfig_type | `query-param` |
| `/v1/nodepools/{nodepool_id}` | `nodepool_id` | `nodepool_id`→ske-nodepool | no | `cat1-auto` |
| `/v1/nodepools/{nodepool_id}/nodes` | `nodepool_id` | `nodepool_id`→ske-nodepool | no | `cat1-auto` |

### support  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/inquiries/{inquiry_id}` | `inquiry_id` | `inquiry_id`→support-inquiry | no | `cat1-auto` |
| `/v1/service-requests/{service_request_id}` | `service_request_id` | `service_request_id`→support-service-request | no | `cat1-auto` |

### vpn  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/vpn-gateways/{vpn_gateway_id}` | `vpn_gateway_id` | `vpn_gateway_id`→vpn-gateway | no | `cat1-auto` |
| `/v1/vpn-tunnels/{vpn_tunnel_id}` | `vpn_tunnel_id` | `vpn_tunnel_id`→vpn-tunnel | no | `cat1-auto` |

