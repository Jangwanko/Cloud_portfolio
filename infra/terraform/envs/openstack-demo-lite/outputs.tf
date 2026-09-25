output "floating_ip" {
  value = openstack_networking_floatingip_v2.vm.address
}
output "demo_url" {
  value = "http://${openstack_networking_floatingip_v2.vm.address}:30080/demo/order-dashboard.html"
}
output "instance_id" {
  value = openstack_compute_instance_v2.vm.id
}
