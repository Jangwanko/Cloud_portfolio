variable "name_prefix" {
  type = string
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

variable "kafka_version" {
  type = string
}

variable "broker_instance_type" {
  type = string
}

variable "broker_volume_size" {
  type = number
}

variable "broker_count" {
  type = number
}
