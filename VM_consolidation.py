import docker
import threading
import time
from queue import Queue
from docker import APIClient
import logging

# docker client
client = docker.from_env()

# dtatic thresholds for host load (average across Vm and resources on each VM)
THR_max_cpu = 55  # Upper CPU threshold percentage
THR_min_cpu = 20  # Lower CPU threshold percentage
THR_max_mem = 55  # Upper memory threshold percentage
THR_min_mem = 20  # Lower memory threshold percentage

# setup queue to hold overloaded VM nodes
migration_queue = Queue()

# thread lock for sync to shared resources
lock = threading.Lock()

# logging config
def setup_logging():
    logging.basicConfig(
        filename='vm_migration.log',  
        filemode='w',
        format='%(asctime)s - %(levelname)s - %(message)s',  # ts, log level, msg
        level=logging.INFO  # log level to INFO
    )

# Node class to hold the status and utilization of each host and VM
# status is {normal, overloaded, underloaded}
class Node:
    def __init__(self, name, cpu, memory, status='normal'):
        self.name = name
        self.cpu = cpu
        self.memory = memory
        self.status = status


def calculate_utilization(container):
    '''
    Calculate the CPU and memory utilization percentages for a given Docker container

    Function gets a container current stats, computes the CPU utilization by
    finding the difference in CPU usage since the last stats read, and calculates the memory
    utilization based on current usage and the total available memory limit for the container

    Parameters:
    - container (docker.models.containers.Container): input container to calculate utilization

    Returns:
    - cpu_percent (float): CPU utilization percentage of the container
    - memory_percent (float): memory utilization percentage of the container

    CPU utilization is calculated as the difference in total CPU usage divided by the difference
    in system CPU usage, multiplied by the number of online CPUs, and then multiplied by 100 to get
    percentage.

    The memory utilization is calculated as the current memory usage divided by the memory limit,
    multiplied by 100 to get percentage.

    If the pre-read CPU statistics are not available,  CPU utilization is calculated as zero 
    to avoid division by zero errors.
    '''
    stats = container.stats(stream=False)
    cpu_percent = 0.0
    memory_percent = 0.0

    if 'system_cpu_usage' in stats['cpu_stats'] and 'precpu_stats' in stats and \
       'cpu_usage' in stats['precpu_stats']:
        cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                    stats['precpu_stats']['cpu_usage'].get('total_usage', 0)
        system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                       stats['precpu_stats'].get('system_cpu_usage', 0)
        if system_delta > 0 and cpu_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * stats['cpu_stats']['online_cpus'] * 100

    if 'usage' in stats['memory_stats'] and 'limit' in stats['memory_stats']:
        memory_percent = (stats['memory_stats']['usage'] / stats['memory_stats']['limit']) * 100

    return cpu_percent, memory_percent


def calculate_average_load(vms):
    """
    Calculate average CPU and memory util for a list of VMs (containers)

    Function iterates over each VM, computes its CPU and memory utilization, 
    and calculates the average values across list of input VMs

    Parameters:
    - vms (list): list of VM (container) objects to calculate the average load

    Returns:
    - avg_cpu (float): average CPU utilization percentage across all VMs
    - avg_mem (float): average memory utilization percentage across all VMs
    """
    total_cpu = total_mem = 0
    for vm in vms:
        cpu, mem = calculate_utilization(vm)
        total_cpu += cpu
        total_mem += mem
    avg_cpu = total_cpu / len(vms)
    avg_mem = total_mem / len(vms)
    return avg_cpu, avg_mem

# function to set host sttus basis static threshold
def determine_host_status(avg_cpu, avg_mem):
    if avg_cpu > THR_max_cpu or avg_mem > THR_max_mem:
        return 'overloaded'
    elif avg_cpu < THR_min_cpu and avg_mem < THR_min_mem:
        return 'underloaded'
    else:
        return 'normal'

# Function to monitor and calculate the load for each host
def monitor_hosts():
    """
    Continuously monitor and evaluate the load status of each VM for eah host

    Function lists all containers that match the 'host*' pattern, groups them by their associated host,
    calculates the average CPU and memory utilization for the VMs on each host, and determines the host's
    load status as overloaded, underloaded, or normal based on static thresholds. Overloaded VMs are
    identified and enqueued for migration.

    The function updates the global `host_loads` dictionary with current load and status for each host in each iteration
    Also prints this information to the console. Set up a 3 second pause between iteration
    """
    while True:
        hosts = client.containers.list(all=True, filters={'name': 'host*'})
        for host in set(container.labels['com.docker.compose.project'] for container in hosts):
            host_vms = [container for container in hosts if container.labels['com.docker.compose.project'] == host]
            avg_cpu, avg_mem = calculate_average_load(host_vms)
            host_status = determine_host_status(avg_cpu, avg_mem)
            
            with lock:
                host_loads[host] = Node(host, avg_cpu, avg_mem, host_status)  # init VM Node object
                if host_status == 'overloaded':
                    max_vm = max(host_vms, key=lambda vm: calculate_utilization(vm)[0] + calculate_utilization(vm)[1])  ## VM with max resource util
                    migration_queue.put(max_vm)
            
            logging.info(f"Host: {host}, Status: {host_status}, Average CPU: {avg_cpu:.2f}%, Average Memory: {avg_mem:.2f}%")
            print(f"Host: {host}, Status: {host_status}, Average CPU: {avg_cpu:.2f}%, Average Memory: {avg_mem:.2f}%")
        time.sleep(3)


def handle_migration():
    """
    Continuously handle the migration of VMs from overloaded hosts

    Function checks for VMs in the migration queue and performs the migration process by stopping the VM,
    committing its state to a new image, and starting a new VM from that image on the same host. The migration
    is simulated on the local Docker host for simplicity.

    The function runs in an infinite loop, checking the migration queue every 2 seconds for new VMs to migrate.
    """
    # init low-level API client
    api_client = APIClient(base_url='unix://var/run/docker.sock')
    while True:
        if not migration_queue.empty():
            with lock: # acquire lock
                vm_migrate = migration_queue.get()
                
                # this is a placeholder for simulating the target host
                # in future a secure docker daemon socket will be used to handle this migration to the target host using TCP 
                target_host = 'localhost'  

                # stop the VM on the current host
                vm_migrate.stop()
                logging.info(f"Stopped {vm_migrate.name} for migration")
                print(f"Stopped {vm_migrate.name} for migration")

                # commit container state to a new image
                # migrated vm prefixed with migrated-*
                new_image = api_client.commit(container=vm_migrate.id, repository=f"migrated-{vm_migrate.name}")
                logging.info(f"Committed {vm_migrate.name} to a new image")
                print(f"Committed {vm_migrate.name} to a new image")

                # start a new VM from the migrated image on the same Docker host
                migrated_container = client.containers.run(new_image['Id'], name=f"migrated-{vm_migrate.name}", detach=True)
                logging.info(f"Migrated {vm_migrate.name} to {migrated_container.name} on {target_host}")
                print(f"Migrated {vm_migrate.name} to {migrated_container.name} on {target_host}")

        time.sleep(2)

def main():
    """
    Main entrypoint

    Function initializes and starts the threads for monitoring host loads and handling VM migrations
    """
    setup_logging() # setup logger

    # dict to hold current load and status
    global host_loads
    host_loads = {}

    # start monitoring thread
    monitor_thread = threading.Thread(target=monitor_hosts)
    monitor_thread.start()

    # start migration thread
    migration_thread = threading.Thread(target=handle_migration)
    migration_thread.start()

    # join
    monitor_thread.join()
    migration_thread.join()

if __name__ == "__main__":
    main()