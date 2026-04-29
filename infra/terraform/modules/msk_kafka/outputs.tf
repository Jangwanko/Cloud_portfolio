output "cluster_arn" {
  value = aws_msk_cluster.this.arn
}

output "bootstrap_brokers" {
  value = aws_msk_cluster.this.bootstrap_brokers
}

output "bootstrap_brokers_tls" {
  value = aws_msk_cluster.this.bootstrap_brokers_tls
}

output "security_group_id" {
  value = aws_security_group.kafka.id
}
