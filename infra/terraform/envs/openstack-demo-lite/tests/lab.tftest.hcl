mock_provider "openstack" {
  mock_data "openstack_networking_network_v2" {
    defaults = { id = "external-network-id" }
  }
  mock_data "openstack_images_image_v2" {
    defaults = { id = "ubuntu-image-id" }
  }
  mock_data "openstack_compute_flavor_v2" {
    defaults = { id = "medium-flavor-id", ram = 4096, disk = 40, vcpus = 2 }
  }
}
variables {
  ssh_public_key = "ssh-ed25519 AAAATESTONLY"
  resize_vcpus   = null
}
run "isolated_lab_plan" {
  command = plan
  assert {
    condition     = openstack_networking_router_v2.lab.external_network_id == "external-network-id"
    error_message = "Lab router must use the retained external network."
  }
  assert {
    condition     = openstack_compute_instance_v2.vm.image_id == "ubuntu-image-id" && openstack_compute_instance_v2.vm.flavor_id == "medium-flavor-id"
    error_message = "VM must use existing image and flavor."
  }
  assert {
    condition     = length(openstack_networking_secgroup_rule_v2.access) == 2 && openstack_networking_secgroup_rule_v2.access["30080"].remote_ip_prefix == "192.168.100.1/32"
    error_message = "Only SSH and demo HTTP should be opened to the operator."
  }
  assert {
    condition     = yamldecode(openstack_compute_instance_v2.vm.user_data).runcmd[0][2] == var.source_commit
    error_message = "Cloud-init must use the pinned application commit."
  }
}
run "reject_public_access" {
  command = plan
  variables {
    operator_cidr = "0.0.0.0/0"
  }
  expect_failures = [var.operator_cidr]
}
run "reject_unpinned_source" {
  command = plan
  variables {
    source_commit = "demo-lite"
  }
  expect_failures = [var.source_commit]
}
