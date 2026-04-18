output "ecr_repository_url" {
  value = module.ecr.repository_url
}

output "eks_cluster_name" {
  value = module.eks.cluster_name
}

output "eks_cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "rds_endpoint" {
  value = module.rds_postgres.endpoint
}

output "redis_primary_endpoint" {
  value = module.elasticache_redis.primary_endpoint
}

output "secrets_arns" {
  value = module.secrets.secret_arns
}

output "certificate_arn" {
  value = local.enable_dns ? module.route53_acm[0].certificate_arn : null
}
