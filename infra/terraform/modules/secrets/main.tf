resource "random_password" "jwt_secret" {
  count   = var.jwt_secret_override == "" ? 1 : 0
  length  = 48
  special = false
}

resource "random_password" "grafana_admin_password" {
  count   = var.grafana_admin_password == "" ? 1 : 0
  length  = 20
  special = false
}

locals {
  jwt_secret             = var.jwt_secret_override != "" ? var.jwt_secret_override : random_password.jwt_secret[0].result
  effective_grafana_pass = var.grafana_admin_password != "" ? var.grafana_admin_password : random_password.grafana_admin_password[0].result

  secrets = {
    app_auth = {
      name  = "${var.name_prefix}/app/auth"
      value = jsonencode({ jwt_secret = local.jwt_secret })
    }
    grafana = {
      name  = "${var.name_prefix}/grafana/admin"
      value = jsonencode({
        username = var.grafana_admin_user
        password = local.effective_grafana_pass
      })
    }
    database = {
      name  = "${var.name_prefix}/database/postgres"
      value = jsonencode({
        username = var.db_username
        password = var.db_password
      })
    }
    redis = {
      name  = "${var.name_prefix}/redis/auth"
      value = jsonencode({ auth_token = var.redis_auth_token })
    }
  }
}

resource "aws_secretsmanager_secret" "this" {
  for_each = local.secrets

  name = each.value.name
}

resource "aws_secretsmanager_secret_version" "this" {
  for_each = local.secrets

  secret_id     = aws_secretsmanager_secret.this[each.key].id
  secret_string = each.value.value
}
