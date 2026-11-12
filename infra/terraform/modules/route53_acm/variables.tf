variable "zone_name" {
  type = string
}

variable "domain_name" {
  type = string
}

variable "record_names" {
  type = list(string)
}
variable "certificate_mode" {
  type        = string
  description = "dns requests public ACM issuance; imported uses a certificate issued outside ACM"
  default     = "dns"

  validation {
    condition     = contains(["dns", "imported"], var.certificate_mode)
    error_message = "certificate_mode must be dns or imported."
  }
}

variable "certificate_body_pem" {
  type      = string
  default   = ""
  sensitive = true
}

variable "certificate_private_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "certificate_chain_pem" {
  type      = string
  default   = ""
  sensitive = true
}
