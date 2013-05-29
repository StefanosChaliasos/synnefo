import logging

from socket import getfqdn
from functools import wraps
from django import dispatch
from django.db import transaction
from django.utils import simplejson as json

from snf_django.lib.api import faults
from synnefo import settings
from synnefo import quotas
from synnefo.api import util
from synnefo.logic import backend
from synnefo.logic.backend_allocator import BackendAllocator
from synnefo.logic.rapi import GanetiApiError
from synnefo.db.models import (NetworkInterface, VirtualMachine,
                               VirtualMachineMetadata)
from synnefo.db.pools import EmptyPool

from vncauthproxy.client import request_forwarding as request_vnc_forwarding

log = logging.getLogger(__name__)

# server creation signal
server_created = dispatch.Signal(providing_args=["created_vm_params"])


def validate_server_action(vm, action):
    if vm.deleted:
        raise faults.BadRequest("Server '%s' has been deleted." % vm.id)

    # Destroyin a server should always be permitted
    if action == "DESTROY":
        return

    # Check that there is no pending action
    pending_action = vm.task
    if pending_action:
        if pending_action == "BUILD":
            raise faults.BuildInProgress("Server '%s' is being build." % vm.id)
        raise faults.BadRequest("Can not perform '%s' action while there is a"
                                " pending '%s'." % (action, pending_action))

    # Check if action can be performed to VM's operstate
    operstate = vm.operstate
    if operstate == "BUILD":
        raise faults.BuildInProgress("Server '%s' is being build." % vm.id)
    elif (action == "START" and operstate == "STARTED") or\
         (action == "STOP" and operstate == "STOPPED") or\
         (action == "RESIZE" and operstate == "STARTED"):
        raise faults.BadRequest("Can not perform '%s' action while server is"
                                " in '%s' state." % (action, operstate))
    return


def server_command(action):
    """Handle execution of a server action.

    Helper function to validate and execute a server action, handle quota
    commission and update the 'task' of the VM in the DB.

    1) Check if action can be performed. If it can, then there must be no
       pending task (with the exception of DESTROY).
    2) Handle previous commission if unresolved:
       * If it is not pending and it to accept, then accept
       * If it is not pending and to reject or is pending then reject it. Since
       the action can be performed only if there is no pending task, then there
       can be no pending commission. The exception is DESTROY, but in this case
       the commission can safely be rejected, and the dispatcher will generate
       the correct ones!
    3) Issue new commission and associate it with the VM. Also clear the task.
    4) Send job to ganeti
    5) Update task and commit
    """
    def decorator(func):
        @wraps(func)
        @transaction.commit_on_success
        def wrapper(vm, *args, **kwargs):
            user_id = vm.userid
            validate_server_action(vm, action)

            # Resolve(reject) previous serial if it is still pending!!
            previous_serial = vm.serial
            if previous_serial and not previous_serial.resolved:
                quotas.resolve_vm_commission(serial=previous_serial)

            # Check if action is quotable and issue the corresponding
            # commission
            serial = None
            commission_info = quotas.get_commission_info(vm, action=action)
            if commission_info is not None:
                # Issue new commission, associate it with the VM
                serial = quotas.issue_commission(user=user_id,
                                                 source=quotas.DEFAULT_SOURCE,
                                                 provisions=commission_info,
                                                 force=False,
                                                 auto_accept=False)
            vm.serial = serial

            # Send the job to Ganeti and get the associated jobID
            try:
                job_id = func(vm, *args, **kwargs)
            except Exception as e:
                if vm.serial is not None:
                    # Since the job never reached Ganeti, reject the commission
                    log.debug("Rejecting commission: '%s', could not perform"
                              " action '%s': %s" % (vm.serial,  action, e))
                    transaction.rollback()
                    quotas.reject_serial(vm.serial)
                    transaction.commit()
                raise

            log.info("user: %s, vm: %s, action: %s, job_id: %s, serial: %s",
                     user_id, vm.id, action, job_id, vm.serial)

            # store the new task in the VM
            if job_id is not None:
                vm.task = action
                vm.task_job_id = job_id
            vm.save()

            return vm
        return wrapper
    return decorator


@transaction.commit_manually
def create(userid, name, password, flavor, image, metadata={},
           personality=[], network=None, use_backend=None):
    if use_backend is None:
        # Allocate backend to host the server. Commit after allocation to
        # release the locks hold by the backend allocator.
        try:
            backend_allocator = BackendAllocator()
            use_backend = backend_allocator.allocate(userid, flavor)
            if use_backend is None:
                log.error("No available backend for VM with flavor %s", flavor)
                raise faults.ServiceUnavailable("No available backends")
        except:
            transaction.rollback()
            raise
        else:
            transaction.commit()

    # Fix flavor for archipelago
    disk_template, provider = util.get_flavor_provider(flavor)
    if provider:
        flavor.disk_template = disk_template
        flavor.disk_provider = provider
        flavor.disk_origin = None
        if provider == 'vlmc':
            flavor.disk_origin = image['checksum']
            image['backend_id'] = 'null'
    else:
        flavor.disk_provider = None

    try:
        if network is None:
            # Allocate IP from public network
            (network, address) = util.get_public_ip(use_backend)
            nic = {'ip': address, 'network': network.backend_id}
        else:
            address = util.get_network_free_address(network)

        # We must save the VM instance now, so that it gets a valid
        # vm.backend_vm_id.
        vm = VirtualMachine.objects.create(
            name=name,
            backend=use_backend,
            userid=userid,
            imageid=image["id"],
            flavor=flavor,
            action="CREATE")

        # Create VM's public NIC. Do not wait notification form ganeti hooks to
        # create this NIC, because if the hooks never run (e.g. building error)
        # the VM's public IP address will never be released!
        NetworkInterface.objects.create(machine=vm, network=network, index=0,
                                        ipv4=address, state="BUILDING")

        log.info("Created entry in DB for VM '%s'", vm)

        # dispatch server created signal
        server_created.send(sender=vm, created_vm_params={
            'img_id': image['backend_id'],
            'img_passwd': password,
            'img_format': str(image['format']),
            'img_personality': json.dumps(personality),
            'img_properties': json.dumps(image['metadata']),
        })

        # Also we must create the VM metadata in the same transaction.
        for key, val in metadata.items():
            VirtualMachineMetadata.objects.create(
                meta_key=key,
                meta_value=val,
                vm=vm)
        # Issue commission to Quotaholder and accept it since at the end of
        # this transaction the VirtualMachine object will be created in the DB.
        # Note: the following call does a commit!
        quotas.issue_and_accept_commission(vm)
    except:
        transaction.rollback()
        raise
    else:
        transaction.commit()

    try:
        jobID = backend.create_instance(vm, nic, flavor, image)
        # At this point the job is enqueued in the Ganeti backend
        vm.backendjobid = jobID
        vm.task = "BUILD"
        vm.task_job_id = jobID
        vm.save()
        transaction.commit()
        log.info("User %s created VM %s, NIC %s, Backend %s, JobID %s",
                 userid, vm, nic, backend, str(jobID))
    except GanetiApiError as e:
        log.exception("Can not communicate to backend %s: %s.",
                      backend, e)
        # Failed while enqueuing OP_INSTANCE_CREATE to backend. Restore
        # already reserved quotas by issuing a negative commission
        vm.operstate = "ERROR"
        vm.backendlogmsg = "Can not communicate to backend."
        vm.deleted = True
        vm.save()
        quotas.issue_and_accept_commission(vm, delete=True)
        raise
    except:
        transaction.rollback()
        raise

    return vm


@server_command("DESTROY")
def destroy(vm):
    log.info("Deleting VM %s", vm)
    return backend.delete_instance(vm)


@server_command("START")
def start(vm):
    log.info("Starting VM %s", vm)
    return backend.startup_instance(vm)


@server_command("STOP")
def stop(vm):
    log.info("Stopping VM %s", vm)
    return backend.shutdown_instance(vm)


@server_command("REBOOT")
def reboot(vm, reboot_type):
    if reboot_type not in ("SOFT", "HARD"):
        raise faults.BadRequest("Malformed request. Invalid reboot"
                                " type %s" % reboot_type)
    log.info("Rebooting VM %s. Type %s", vm, reboot_type)

    return backend.reboot_instance(vm, reboot_type.lower())


@server_command("RESIZE")
def resize(vm, flavor):
    old_flavor = vm.flavor
    # User requested the same flavor
    if old_flavor.id == flavor.id:
        raise faults.BadRequest("Server '%s' flavor is already '%s'."
                                % (vm, flavor))
        return None
    # Check that resize can be performed
    if old_flavor.disk != flavor.disk:
        raise faults.BadRequest("Can not resize instance disk.")
    if old_flavor.disk_template != flavor.disk_template:
        raise faults.BadRequest("Can not change instance disk template.")

    log.info("Resizing VM from flavor '%s' to '%s", old_flavor, flavor)
    commission_info = {"cyclades.cpu": flavor.cpu - old_flavor.cpu,
                       "cyclades.ram": flavor.ram - old_flavor.ram}
    # Save serial to VM, since it is needed by server_command decorator
    vm.serial = quotas.issue_commission(user=vm.userid,
                                        source=quotas.DEFAULT_SOURCE,
                                        provisions=commission_info)
    return backend.resize_instance(vm, vcpus=flavor.cpu, memory=flavor.ram)


@server_command("SET_FIREWALL_PROFILE")
def set_firewall_profile(vm, profile):
    log.info("Setting VM %s firewall %s", vm, profile)

    if profile not in [x[0] for x in NetworkInterface.FIREWALL_PROFILES]:
        raise faults.BadRequest("Unsupported firewall profile")
    backend.set_firewall_profile(vm, profile)
    return None


@server_command("CONNECT")
def connect(vm, network):
    if network.state != 'ACTIVE':
        raise faults.BuildInProgress('Network not active yet')

    address = None
    if network.dhcp:
        # Get a free IP from the address pool.
        try:
            address = util.get_network_free_address(network)
        except EmptyPool:
            raise faults.OverLimit('Network is full')

    log.info("Connecting VM %s to Network %s(%s)", vm, network, address)

    return backend.connect_to_network(vm, network, address)


@server_command("DISCONNECT")
def disconnect(vm, nic_index):
    nic = util.get_nic_from_index(vm, nic_index)

    log.info("Removing NIC %s from VM %s", str(nic.index), vm)

    if nic.dirty:
        raise faults.BuildInProgress('Machine is busy.')
    else:
        vm.nics.all().update(dirty=True)

    return backend.disconnect_from_network(vm, nic)


def console(vm, console_type):
    """Arrange for an OOB console of the specified type

    This method arranges for an OOB console of the specified type.
    Only consoles of type "vnc" are supported for now.

    It uses a running instance of vncauthproxy to setup proper
    VNC forwarding with a random password, then returns the necessary
    VNC connection info to the caller.

    """
    log.info("Get console  VM %s, type %s", vm, console_type)

    # Use RAPI to get VNC console information for this instance
    if vm.operstate != "STARTED":
        raise faults.BadRequest('Server not in ACTIVE state.')

    if settings.TEST:
        console_data = {'kind': 'vnc', 'host': 'ganeti_node', 'port': 1000}
    else:
        console_data = backend.get_instance_console(vm)

    if console_data['kind'] != 'vnc':
        message = 'got console of kind %s, not "vnc"' % console_data['kind']
        raise faults.ServiceUnavailable(message)

    # Let vncauthproxy decide on the source port.
    # The alternative: static allocation, e.g.
    # sport = console_data['port'] - 1000
    sport = 0
    daddr = console_data['host']
    dport = console_data['port']
    password = util.random_password()

    if settings.TEST:
        fwd = {'source_port': 1234, 'status': 'OK'}
    else:
        fwd = request_vnc_forwarding(sport, daddr, dport, password)

    if fwd['status'] != "OK":
        raise faults.ServiceUnavailable('vncauthproxy returned error status')

    # Verify that the VNC server settings haven't changed
    if not settings.TEST:
        if console_data != backend.get_instance_console(vm):
            raise faults.ServiceUnavailable('VNC Server settings changed.')

    console = {
        'type': 'vnc',
        'host': getfqdn(),
        'port': fwd['source_port'],
        'password': password}

    return console
