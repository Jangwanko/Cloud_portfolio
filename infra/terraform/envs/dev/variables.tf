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

# AWS support source: https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html
variable "cluster_version" {
  type        = string
  description = "EKS standard-support version; review the AWS EKS release calendar before changing"
  default     = "1.36"

  validation {
    condition     = contains(["1.33", "1.34", "1.35", "1.36"], var.cluster_version)
    error_message = "Use an EKS version in standard support as of 2026-07-14: 1.33, 1.34, 1.35, or 1.36."
  }
}

variable "eks_endpoint_public_access" {
  type        = bool
  description = "Enable the public EKS API endpoint only when restricted CIDRs are supplied"
  default     = false
}

variable "eks_endpoint_public_access_cidrs" {
  type        = list(string)
  description = "Restricted IPv4 CIDRs allowed to access the public EKS API endpoint"
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.eks_endpoint_public_access_cidrs :
      can(cidrnetmask(cidr)) && cidr != "0.0.0.0/0"
    ])
    error_message = "Public EKS endpoint CIDRs must be valid restricted IPv4 CIDRs; 0.0.0.0/0 is forbidden."
  }
}

variable "enable_cluster_creator_admin_permissions" {
  type        = bool
  description = "Grant the Terraform cluster creator bootstrap administrator access for this dev blueprint"
  default     = true
}

variable "node_instance_types" {
  type        = list(string)
  description = "EKS node instance types"
  default     = ["t3.large"]
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 4
}

variable "db_name" {
  type    = string
  default = "portfolio"
}

variable "db_username" {
  type    = string
  default = "portfolio"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage" {
  type    = number
  default = 50
}

variable "db_backup_retention_period" {
  type    = number
  default = 7
}

# AWS support source: https://docs.aws.amazon.com/msk/latest/developerguide/supported-kafka-versions.html
variable "kafka_version" {
  type        = string
  description = "Amazon MSK version on the AWS recommended support line"
  default     = "3.9.x"

  validation {
    condition     = contains(["3.8.x", "3.9.x", "4.0.x", "4.1.x"], var.kafka_version)
    error_message = "Use an Amazon MSK version supported as of 2026-07-14: 3.8.x, 3.9.x, 4.0.x, or 4.1.x."
  }
}

variable "kafka_broker_instance_type" {
  type    = string
  default = "kafka.t3.small"
}

variable "kafka_broker_volume_size" {
  type    = number
  default = 20
}

variable "kafka_broker_count" {
  type        = number
  description = "MSK broker count. Keep this aligned with az_count for the portfolio HA baseline."
  default     = 3
}

variable "grafana_admin_user" {
  type    = string
  default = "admin"
}

variable "grafana_admin_password" {
  type      = string
  default   = ""
  sensitive = true
}

variable "jwt_secret_override" {
  type      = string
  default   = ""
  sensitive = true
}

variable "route53_zone_name" {
  type    = string
  default = ""
}

variable "domain_name" {
  type    = string
  default = ""
}
