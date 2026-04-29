variable "name_prefix" {
  type = string
}

variable "db_username" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "kafka_bootstrap_servers" {
  type = string
}

variable "jwt_secret_override" {
  type      = string
  sensitive = true
}

variable "grafana_admin_user" {
  type = string
}

variable "grafana_admin_password" {
  type      = string
  sensitive = true
}
