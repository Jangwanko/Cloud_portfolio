data "openstack_networking_network_v2" "public" {
  name = var.external_network
}
data "openstack_images_image_v2" "ubuntu" {
  name        = var.image_name
  most_recent = false
}
data "openstack_compute_flavor_v2" "vm" {
  name = var.flavor_name
}
resource "openstack_networking_network_v2" "lab" {
  name           = var.name
  admin_state_up = true
}
resource "openstack_networking_subnet_v2" "lab" {
  name            = "${var.name}-subnet"
  network_id      = openstack_networking_network_v2.lab.id
  cidr            = var.subnet_cidr
  ip_version      = 4
  dns_nameservers = ["1.1.1.1"]
}
resource "openstack_networking_router_v2" "lab" {
  name                = "${var.name}-router"
  external_network_id = data.openstack_networking_network_v2.public.id
}
resource "openstack_networking_router_interface_v2" "lab" {
  router_id = openstack_networking_router_v2.lab.id
  subnet_id = openstack_networking_subnet_v2.lab.id
}
resource "openstack_networking_secgroup_v2" "lab" {
  name        = "${var.name}-access"
  description = "SSH and demo HTTP from the operator only"
}
resource "openstack_networking_secgroup_rule_v2" "access" {
  for_each          = toset(["22", "30080"])
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = tonumber(each.value)
  port_range_max    = tonumber(each.value)
  remote_ip_prefix  = var.operator_cidr
  security_group_id = openstack_networking_secgroup_v2.lab.id
}
resource "openstack_networking_port_v2" "vm" {
  name               = "${var.name}-port"
  network_id         = openstack_networking_network_v2.lab.id
  security_group_ids = [openstack_networking_secgroup_v2.lab.id]
  fixed_ip {
    subnet_id = openstack_networking_subnet_v2.lab.id
  }
}
resource "openstack_networking_floatingip_v2" "vm" {
  pool = var.external_network
}
resource "openstack_networking_floatingip_associate_v2" "vm" {
  floating_ip = openstack_networking_floatingip_v2.vm.address
  port_id     = openstack_networking_port_v2.vm.id
  depends_on  = [openstack_networking_router_interface_v2.lab]
}
resource "openstack_compute_instance_v2" "vm" {
  name      = var.name
  image_id  = data.openstack_images_image_v2.ubuntu.id
  flavor_id = var.resize_vcpus == null ? data.openstack_compute_flavor_v2.vm.id : openstack_compute_flavor_v2.resize[0].id
  key_pair  = var.key_pair
  network {
    port = openstack_networking_port_v2.vm.id
  }
  user_data = "#cloud-config\n${yamlencode({
    ssh_authorized_keys = [var.ssh_public_key]
    package_update      = true
    packages            = ["git", "curl", "python3-yaml"]
    write_files = [
      {
        path        = "/opt/demo/bootstrap.sh"
        permissions = "0700"
        encoding    = "b64"
        content     = filebase64("${path.module}/scripts/bootstrap.sh")
      },
      {
        path        = "/opt/demo/deploy.py"
        permissions = "0700"
        encoding    = "b64"
        content     = filebase64("${path.module}/scripts/deploy.py")
      },
      {
        path        = "/opt/demo/smoke.py"
        permissions = "0700"
        encoding    = "b64"
        content     = filebase64("${path.module}/scripts/smoke.py")
      }
    ]
    runcmd = [["bash", "/opt/demo/bootstrap.sh", var.source_commit, var.k3s_version]]
  })}"
  depends_on = [openstack_networking_floatingip_associate_v2.vm]
}

resource "openstack_compute_flavor_v2" "resize" {
  count     = var.resize_vcpus == null ? 0 : 1
  name      = "${var.name}-cpu${var.resize_vcpus}"
  vcpus     = var.resize_vcpus
  ram       = data.openstack_compute_flavor_v2.vm.ram
  disk      = data.openstack_compute_flavor_v2.vm.disk
  is_public = false
}