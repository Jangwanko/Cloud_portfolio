variable "aws_account_id" {
  description = "Explicit destination account; provider rejects another authenticated account."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Provide the 12-digit AWS account ID."
  }
}

variable "aws_region" {
  description = "AWS region for the external backup bucket."
  type        = string
  default     = "ap-northeast-2"
}

variable "bucket_name" {
  description = "A new globally unique S3 bucket name."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "Use 3-63 lowercase letters, digits or hyphens; start/end with a letter or digit."
  }
}

variable "object_prefix" {
  description = "Prefix granted to the backup client."
  type        = string
  default     = "portfolio-backup-drill"
  validation {
    condition     = can(regex("^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*$", var.object_prefix))
    error_message = "Use nonempty path segments of letters, digits, underscores or hyphens."
  }
}
