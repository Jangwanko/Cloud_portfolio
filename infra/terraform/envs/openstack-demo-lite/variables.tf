variable "cloud" {
  type    = string
  default = "kolla-admin"
}
variable "name" {
  type    = string
  default = "tf-demo-lite"
}
variable "external_network" {
  type    = string
  default = "public"
}
variable "image_name" {
  type    = string
  default = "ubuntu-24.04"
}
variable "flavor_name" {
  type    = string
  default = "lab.medium"
}
variable "key_pair" {
  type    = string
  default = "lab-key"
}
variable "ssh_public_key" {
  description = "Additional operator public key; never provide a private key."
  type        = string
  validation {
    condition     = can(regex("^ssh-(ed25519|rsa) ", var.ssh_public_key))
    error_message = "Provide an OpenSSH public key."
  }
}
variable "operator_cidr" {
  type    = string
  default = "192.168.100.1/32"
  validation {
    condition     = can(cidrnetmask(var.operator_cidr)) && var.operator_cidr != "0.0.0.0/0"
    error_message = "Use a restricted IPv4 operator CIDR."
  }
}
variable "subnet_cidr" {
  type    = string
  default = "10.30.0.0/24"
}
variable "source_commit" {
  type    = string
  default = "3509ed0f272984250f71a9d112450df8ef8b75fe"
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.source_commit))
    error_message = "Pin a full Git commit."
  }
}
variable "k3s_version" {
  type    = string
  default = "v1.36.4+k3s1"
}

variable "resize_vcpus" {
  description = "Optional CPU-only resize; RAM and disk match flavor_name."
  type        = number
  default     = null
  validation {
    condition     = var.resize_vcpus == null ? true : (var.resize_vcpus >= 1 && var.resize_vcpus <= 6 && floor(var.resize_vcpus) == var.resize_vcpus)
    error_message = "Use an integer CPU count from 1 to 6."
  }
}