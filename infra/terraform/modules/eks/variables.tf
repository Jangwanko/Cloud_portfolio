variable "cluster_name" {
  type = string
}

variable "cluster_version" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "cluster_endpoint_public_access" {
  type        = bool
  description = "Whether to expose the EKS API endpoint publicly"
  default     = false
}

variable "cluster_endpoint_public_access_cidrs" {
  type        = list(string)
  description = "Restricted IPv4 CIDRs allowed to reach the public EKS API endpoint"
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.cluster_endpoint_public_access_cidrs :
      can(cidrnetmask(cidr)) && cidr != "0.0.0.0/0"
    ])
    error_message = "Public EKS endpoint CIDRs must be valid restricted IPv4 CIDRs; 0.0.0.0/0 is forbidden."
  }
}

variable "enable_cluster_creator_admin_permissions" {
  type        = bool
  description = "Grant the Terraform cluster creator bootstrap administrator access"
  default     = true
}

variable "node_instance_types" {
  type = list(string)
}

variable "node_desired_size" {
  type = number
}

variable "node_min_size" {
  type = number
}

variable "node_max_size" {
  type = number
}
