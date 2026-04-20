output "endpoint" {
  value = module.db.db_instance_address
}

output "generated_password" {
  value     = random_password.db.result
  sensitive = true
}
