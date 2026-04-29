data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
  azs         = slice(data.aws_availability_zones.available.names, 0, var.az_count)
  enable_dns  = var.route53_zone_name != "" && var.domain_name != ""
}

module "vpc" {
  source = "../../modules/vpc"

  name_prefix = local.name_prefix
  cidr_block  = var.vpc_cidr
  azs         = local.azs
}

module "ecr" {
  source = "../../modules/ecr"

  repository_name = "${local.name_prefix}-app"
}

module "eks" {
  source = "../../modules/eks"

  cluster_name         = "${local.name_prefix}-eks"
  cluster_version      = var.cluster_version
  vpc_id               = module.vpc.vpc_id
  private_subnet_ids   = module.vpc.private_subnet_ids
  node_instance_types  = var.node_instance_types
  node_desired_size    = var.node_desired_size
  node_min_size        = var.node_min_size
  node_max_size        = var.node_max_size
}

module "rds_postgres" {
  source = "../../modules/rds_postgres"

  name_prefix                = local.name_prefix
  db_name                    = var.db_name
  username                   = var.db_username
  instance_class             = var.db_instance_class
  allocated_storage          = var.db_allocated_storage
  backup_retention_period    = var.db_backup_retention_period
  vpc_id                     = module.vpc.vpc_id
  subnet_ids                 = module.vpc.database_subnet_ids
  allowed_security_group_ids = [module.eks.node_security_group_id]
}

module "msk_kafka" {
  source = "../../modules/msk_kafka"

  name_prefix                = local.name_prefix
  vpc_id                     = module.vpc.vpc_id
  subnet_ids                 = module.vpc.private_subnet_ids
  allowed_security_group_ids = [module.eks.node_security_group_id]
  kafka_version              = var.kafka_version
  broker_instance_type       = var.kafka_broker_instance_type
  broker_volume_size         = var.kafka_broker_volume_size
  broker_count               = var.kafka_broker_count
}

module "secrets" {
  source = "../../modules/secrets"

  name_prefix             = local.name_prefix
  db_username             = var.db_username
  db_password             = module.rds_postgres.generated_password
  kafka_bootstrap_servers = module.msk_kafka.bootstrap_brokers
  jwt_secret_override     = var.jwt_secret_override
  grafana_admin_user      = var.grafana_admin_user
  grafana_admin_password  = var.grafana_admin_password
}

module "route53_acm" {
  count = local.enable_dns ? 1 : 0

  source = "../../modules/route53_acm"

  zone_name    = var.route53_zone_name
  domain_name  = var.domain_name
  record_names = [
    "grafana.${var.domain_name}",
    "prometheus.${var.domain_name}",
  ]
}
