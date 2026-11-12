variable "name_prefix" {
  type = string
}

variable "engine_version" {
  type        = string
  description = "PostgreSQL 16 minor version; parameter group family remains postgres16"
  default     = "16.14"

  validation {
    condition     = can(regex("^16\\.[0-9]+$", var.engine_version))
    error_message = "Use a PostgreSQL 16 minor version such as 16.14."
  }
}

variable "port" {
  type        = number
  description = "5432 or null to accept the service default; security group ingress remains PostgreSQL 5432"
  default     = 5432

  validation {
    condition     = var.port == null || var.port == 5432
    error_message = "Use 5432 or null; this module's security group permits PostgreSQL on 5432."
  }
}

variable "db_name" {
  type = string
}

variable "username" {
  type = string
}

variable "instance_class" {
  type = string
}

variable "allocated_storage" {
  type = number
}

variable "backup_retention_period" {
  type = number
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "allowed_security_group_ids" {
  type = list(string)
}
