locals {
  public_subnets = [
    for index, az in var.azs : cidrsubnet(var.cidr_block, 8, index)
  ]

  private_subnets = [
    for index, az in var.azs : cidrsubnet(var.cidr_block, 8, index + 10)
  ]

  database_subnets = [
    for index, az in var.azs : cidrsubnet(var.cidr_block, 8, index + 20)
  ]

  elasticache_subnets = [
    for index, az in var.azs : cidrsubnet(var.cidr_block, 8, index + 30)
  ]
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.7"

  name = var.name_prefix
  cidr = var.cidr_block

  azs               = var.azs
  public_subnets    = local.public_subnets
  private_subnets   = local.private_subnets
  database_subnets  = local.database_subnets
  elasticache_subnets = local.elasticache_subnets

  enable_nat_gateway     = true
  single_nat_gateway     = true
  create_database_subnet_group = true
  create_elasticache_subnet_group = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}
