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

output "msk_cluster_arn" {
  value = module.msk_kafka.cluster_arn
}

output "kafka_bootstrap_brokers" {
  value = module.msk_kafka.bootstrap_brokers
}

output "secrets_arns" {
  value = module.secrets.secret_arns
}

output "certificate_arn" {
  value = local.enable_dns ? module.route53_acm[0].certificate_arn : null
}
