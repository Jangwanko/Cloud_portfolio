terraform {
  required_version = ">= 1.15.8, < 1.16.0"
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "3.3.0"
    }
  }
}
