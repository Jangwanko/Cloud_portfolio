variable "aws_region" {
  type        = string
  description = "AWS region"
  default     = "ap-northeast-2"
}

variable "project_name" {
  type        = string
  description = "Project name prefix"
  default     = "cloud-portfolio"
}

variable "environment" {
  type        = string
  description = "Environment name"
  default     = "dev"
}

variable "vpc_cidr" {
  type        = string
  description = "Primary VPC CIDR"
  default     = "10.50.0.0/16"
}

variable "az_count" {
  type        = number
  description = "How many AZs to use"
  default     = 3
}

variable "cluster_version" {
  type        = string
  description = "EKS version"
  default     = "1.31"
}

variable "node_instance_types" {
  type        = list(string)
  description = "EKS node instance types"
  default     = ["t3.large"]
}

variable "node_desired_size" {
  type        = number
  default     = 2
}

variable "node_min_size" {
  type        = number
  default     = 2
}

variable "node_max_size" {
  type        = number
  default     = 4
}

variable "db_name" {
  type        = string
  default     = "portfolio"
}

variable "db_username" {
  type        = string
  default     = "portfolio"
}

variable "db_instance_class" {
  type        = string
  default     = "db.t4g.medium"
}

variable "db_allocated_storage" {
  type        = number
  default     = 50
}

variable "db_backup_retention_period" {
  type        = number
  default     = 7
}

variable "kafka_version" {
  type        = string
  default     = "3.6.0"
}

variable "kafka_broker_instance_type" {
  type        = string
  default     = "kafka.t3.small"
}

variable "kafka_broker_volume_size" {
  type        = number
  default     = 20
}

variable "kafka_broker_count" {
  type        = number
  description = "MSK broker count. Keep this aligned with az_count for the portfolio HA baseline."
  default     = 3
}

variable "grafana_admin_user" {
  type        = string
  default     = "admin"
}

variable "grafana_admin_password" {
  type        = string
  default     = ""
  sensitive   = true
}

variable "jwt_secret_override" {
  type        = string
  default     = ""
  sensitive   = true
}

variable "route53_zone_name" {
  type        = string
  default     = ""
}

variable "domain_name" {
  type        = string
  default     = ""
}
