# pylint: disable=missing-docstring, redefined-outer-name, protected-access, unused-argument
import os
import re
from multiprocessing.pool import ThreadPool

import pytest
import docker

from library import pl_etcd as etcd


class Fuzzy(unicode):
    def __eq__(self, other):
        return bool(re.match(u"^" + self, other))

    def __ne__(self, other):
        return not self.__eq__(other)


UNSTARTED = Fuzzy('unstarted:.*')


class EtcdDaemon(object):
    def __init__(self, container, peer_port, client_port, name):
        self.container = container
        self.peer_port = peer_port
        self.advertised_peer_url = "http://127.0.0.1:{}".format(peer_port)
        self.client_port = client_port
        self.advertised_client_url = "http://127.0.0.1:{}".format(client_port)
        self.name = name


def start_containers(request, count=1):
    """Starts a fresh etcd cluster, just for you! Feeling special already?"""
    # print "starting", count
    docker_url = os.getenv('DOCKER_URL', 'unix://var/run/docker.sock')
    docker_image = os.getenv('DOCKER_IMAGE', 'quay.io/coreos/etcd:v2.3.0-alpha.0')
    client = docker.Client(base_url=docker_url, version='auto')

    initial_urls = []
    for i in xrange(count):
        # prepare list of urls, all nodes in the cluster need this when staring up
        p_port = 2380 + i
        initial_urls.append("node{i}=http://127.0.0.1:{p_port}".format(i=i, p_port=p_port))

    command = ["-name=node{i}",
               "-advertise-client-urls", "http://127.0.0.1:{c_port}",
               "-listen-client-urls", "http://0.0.0.0:{c_port}",
               "-initial-advertise-peer-urls", "http://127.0.0.1:{p_port}",
               "-listen-peer-urls", "http://0.0.0.0:{p_port}",
               "-initial-cluster-token", "etcd-cluster-1",
               "-initial-cluster", ",".join(initial_urls),
               "-initial-cluster-state", "new"]
    cluster = []
    streams = []
    for i in xrange(count):
        # print "starting node", i
        container = client.create_container(
            image=docker_image,
            command=[c.format(i=i, c_port=4001+i, p_port=2380+i) for c in command],
            name="node{}".format(i),
            host_config=client.create_host_config(
                network_mode='host',  # all nodes will share the host's ip address
            ),
        )
        cluster.append(EtcdDaemon(
            container, peer_port=2380+i, client_port=4001+i, name="node{}".format(i)))

    def cleanup():
        callbacks = []
        for daemon in cluster:
            c_id = daemon.container.get("Id")
            def callback(c_id=c_id):
                # print "stop", c_id
                client.stop(c_id, timeout=0.00001)
                client.remove_container(c_id, force=True)
                # print "stopped", c_id
            callbacks.append(callback)
        pool = ThreadPool(count)
        results = [pool.apply_async(cb) for cb in callbacks]
        [res.wait() for res in results]  # pylint: disable=expression-not-assigned
        # or run the cleanup callbacks in sequence when debugging
        # [c() for c in callbacks]
    request.addfinalizer(cleanup)

    for daemon in cluster:
        client.start(daemon.container.get("Id"))
        streams.append(StreamHandler(client, daemon.container))

    for i, stream in enumerate(streams):
        # print "waiting for confirmation from node%d" % i
        stream.wait(
            re.compile(".* etcdserver: published {{Name:node{} .*".format(i)))
    return client, cluster


@pytest.fixture
def one_node_fixture(request):
    return start_containers(request)


@pytest.fixture
def two_node_fixture(request):
    return start_containers(request, 2)


@pytest.fixture
def three_node_fixture(request):
    return start_containers(request, 3)


class StreamHandler(object):
    def __init__(self, client, container):
        self.client = client
        self.container = container
        self.stream = client.attach(container, stdout=True, stderr=True, logs=True, stream=True)

    def wait(self, regexp):
        """wait until an expected expression is found, or we reach the end of the stream"""
        for chunk in self.stream:
            for line in chunk.splitlines():
                if regexp.match(line):
                    return
                # else:
                #     print ">>", line, "<<"


def test_absent_to_present(one_node_fixture):
    """Start a one node cluster then add second node"""
    handler = etcd.StateHandler(
        name="new-node", state="present", cluster_urls=["http://127.0.0.1:4001"],
        advertised_peer_urls=["http://127.0.0.1:4002"])
    assert handler.cluster_state['names'] == ['node0']
    handler.transition()
    assert sorted(handler.cluster_state['names']) == sorted(['node0', UNSTARTED])


def test_present_to_absent(three_node_fixture):
    """Start 3 node cluster then remove a node"""
    _, cluster = three_node_fixture
    handler = etcd.StateHandler(
        name="node0", state="absent",
        cluster_urls=[daemon.advertised_client_url for daemon in cluster])
    changed, msg = handler.transition()
    assert msg == Fuzzy("removed node .*")
    assert sorted(handler.cluster_state['names']) == sorted(['node1', 'node2'])
    assert changed == True


def test_remove_unstarted(two_node_fixture):
    """Start 2 node cluster, register then remove the third unstarted node"""
    _, cluster = two_node_fixture
    # register new-node
    handler = etcd.StateHandler(
        name="new-node", state="present",
        cluster_urls=[daemon.advertised_client_url for daemon in cluster],
        advertised_peer_urls=['http://example.com:4001'])
    handler.transition()
    assert sorted(handler.cluster_state['names']) == sorted(['node0', 'node1', UNSTARTED])
    # unregister the node
    handler = etcd.StateHandler(
        name="new-node", state="absent",
        cluster_urls=[daemon.advertised_client_url for daemon in cluster],
        advertised_peer_urls=['http://example.com:4001'])
    (changed, msg) = handler.transition()
    assert msg.startswith("removed node")
    assert changed == True
    assert sorted(handler.cluster_state['names']) == sorted(['node0', 'node1'])


def test_add_when_unstarted(three_node_fixture):
    """Tests you can't add a member when you have unstarted nodes in the cluster
    """
    _, cluster = three_node_fixture
    etcd.StateHandler(
        name="new-node", state="present",
        cluster_urls=[daemon.advertised_client_url for daemon in cluster],
        advertised_peer_urls=['http://example.com:4001']).transition()
    handler = etcd.StateHandler(
        name="another-node", state="present",
        cluster_urls=[daemon.advertised_client_url for daemon in cluster],
        advertised_peer_urls=['http://example.com:4002'])
    changed, msg = handler.transition()
    assert sorted(handler.cluster_state['names']) == sorted(['node0', 'node1', 'node2', UNSTARTED])
    assert msg == 'refusing to add member! cluster has 1 unstarted nodes'
    assert changed == False


def test_remove_with_unhealthy(three_node_fixture):
    """Tests you can't remove a started member when you have
    unreachable nodes in the cluster
    """
    docker_client, cluster = three_node_fixture
    container = cluster[2].container
    docker_client.stop(container)
    handler = etcd.StateHandler(
        name="node0", state="absent",
        cluster_urls=[daemon.advertised_client_url for daemon in cluster])
    changed, msg = handler.transition()
    assert msg == "refusing to remove started node, cluster is not healthy!"
    assert changed == False


def test_remove_with_unstarted(two_node_fixture):
    """Tests you can't remove a started member when you have
    unstarted nodes in the cluster
    """
    _, cluster = two_node_fixture
    etcd.StateHandler(
        name="new-node", state="present",
        cluster_urls=[daemon.advertised_client_url for daemon in cluster],
        advertised_peer_urls=['http://example.com:4001']).transition()
    handler = etcd.StateHandler(
        name="node0", state="absent",
        cluster_urls=[daemon.advertised_client_url for daemon in cluster])
    changed, msg = handler.transition()
    assert msg == "refusing to remove started node, cluster is not healthy!"
    assert changed == False
