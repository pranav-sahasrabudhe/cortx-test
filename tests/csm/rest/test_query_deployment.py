# pylint: disable=too-many-lines
# !/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2022 Seagate Technology LLC and/or its Affiliates
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# For any questions about this software or licensing,
# please email opensource@seagate.com or cortx-questions@seagate.com.
#
"""Tests Query Deployment scenarios using REST API
"""

import time
import logging

import random
import string
from http import HTTPStatus
import pytest

from commons import configmanager, cortxlogging
from commons.constants import K8S_SCRIPTS_PATH, K8S_PRE_DISK, POD_NAME_PREFIX
from libs.csm.csm_interface import csm_api_factory
from libs.ha.ha_common_libs_k8s import HAK8s
from libs.prov.prov_k8s_cortx_deploy import ProvDeployK8sCortxLib


class TestQueryDeployment():
    """Query Deployment Testsuites"""

    @classmethod
    def setup_class(cls):
        """Setup class"""
        cls.log = logging.getLogger(__name__)
        cls.ha_obj = HAK8s()
        cls.deploy_lc_obj = ProvDeployK8sCortxLib()
        cls.csm_obj = csm_api_factory("rest")
        cls.csm_conf = configmanager.get_config_wrapper(
            fpath="config/csm/test_rest_query_deployment.yaml")
        cls.deploy_start_time = None
        cls.deploy_end_time = None
        cls.update_seconds = cls.csm_conf["update_seconds"]

    def setup_method(self):
        """
        Setup method
        """
        self.log.info("Prerequisite: Deploy cortx cluster")
        self.log.info("Cleanup: Destroying the cluster ")
        resp = self.deploy_lc_obj.destroy_setup(self.csm_obj.master, self.csm_obj.worker_list,
                                                K8S_SCRIPTS_PATH)
        assert resp[0], resp[1]
        self.log.info("Cleanup: Cluster destroyed successfully")
        self.deploy_start_time = time.time()
        self.log.info("Printing start time for deployment %s: ", self.deploy_start_time)
        self.log.info("Cleanup: Setting prerequisite")
        self.deploy_lc_obj.execute_prereq_cortx(self.csm_obj.master,
                                                K8S_SCRIPTS_PATH,
                                                K8S_PRE_DISK)

        for node in self.csm_obj.worker_list:
            self.deploy_lc_obj.execute_prereq_cortx(node, K8S_SCRIPTS_PATH,
                                                    K8S_PRE_DISK)
        self.log.info("Cleanup: Prerequisite set successfully")

        self.log.info("Cleanup: Deploying the Cluster")
        resp_cls = self.deploy_lc_obj.deploy_cluster(self.csm_obj.master,
                                                     K8S_SCRIPTS_PATH)
        assert resp_cls[0], resp_cls[1]
        self.log.info("Cleanup: Cluster deployment successfully")

        self.log.info("[Start] Sleep %s", self.update_seconds)
        time.sleep(self.update_seconds)
        self.log.info("[Start] Sleep %s", self.update_seconds)

        self.log.info("Cleanup: Check cluster status")
        resp = self.ha_obj.poll_cluster_status(self.csm_obj.master)
        assert resp[0], resp[1]
        self.log.info("Cleanup: Cluster status checked successfully")
        self.deploy_end_time = time.time()
        self.log.info("Printing end time for deployment %s: ", self.deploy_end_time)

    @pytest.mark.lc
    @pytest.mark.csmrest
    @pytest.mark.cluster_user_ops
    @pytest.mark.tags('TEST-45675')
    def test_45675(self):
        """
        Verify GET cluster topology with valid storage id
        """
        test_case_name = cortxlogging.get_frame()
        self.log.info("##### Test started -  %s #####", test_case_name)
        self.log.info("Step 1: Send GET request for fetching system topology"
                      "without storage set id")
        resp, result, err_msg = self.csm_obj.verify_storage_set()
        assert result, err_msg
        self.log.info("Response : %s", err_msg)
        self.log.info("Step 2: Send GET request for fetching system topology"
                    "with storage set id")
        storage_sets = resp.json()["topology"]["storage_sets"]
        for storage_set_id in storage_sets:
            self.log.info("Sending request for %s ", storage_set_id)
            resp, result, err_msg = self.csm_obj.verify_storage_set(
                                      storage_set_id = storage_set_id['id'])
            assert result, err_msg
            self.log.info("Response : %s", resp)
        self.log.info("##### Test ended -  %s #####", test_case_name)

    @pytest.mark.lc
    @pytest.mark.csmrest
    @pytest.mark.cluster_user_ops
    @pytest.mark.tags('TEST-45671')
    def test_45671(self):
        """
        Verify GET cluster topology with invalid resource name
        """
        test_case_name = cortxlogging.get_frame()
        self.log.info("##### Test started -  %s #####", test_case_name)
        test_cfg = self.csm_conf["test_45671"]
        resp_error_code = test_cfg["error_code"]
        resp_msg_id = test_cfg["message_id"]
        resp_msg_index = test_cfg["message_index"]
        self.log.info("Step 1: Send GET request with invalid resource ID")
        random_string = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
        random_number = self.csm_obj.random_gen.randrange(1, 99999)
        random_symbols = ''.join(random.choices(string.punctuation, k=10))
        invalid_ids = []
        invalid_ids = ['Clusters', random_string, random_number, random_symbols]
        self.log.info("Printing list of invalid resource names: %s", invalid_ids)
        for ids in invalid_ids:
            self.log.info("Sending request for: %s", ids)
            resp = self.csm_obj.get_system_topology(uri_param = str(ids))
            assert resp.status_code == HTTPStatus.NOT_FOUND, \
                               "Status code check failed for get system topology"
            resp, err_msg = self.csm_obj.verify_error_message(resp, resp_error_code, resp_msg_id,
                                                     resp_msg_index)
            assert resp, err_msg
        self.log.info("##### Test ended -  %s #####", test_case_name)

    @pytest.mark.lc
    @pytest.mark.csmrest
    @pytest.mark.cluster_user_ops
    @pytest.mark.tags('TEST-45676')
    def test_45676(self):
        """
        Verify GET cluster topology with invalid storage set id
        """
        test_case_name = cortxlogging.get_frame()
        self.log.info("##### Test started -  %s #####", test_case_name)
        test_cfg = self.csm_conf["test_45676"]
        resp_error_code = test_cfg["error_code"]
        resp_msg_id = test_cfg["message_id"]
        resp_msg_index = test_cfg["message_index"]
        random_string = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
        random_number = self.csm_obj.random_gen.randrange(1, 99999)
        random_symbols = ''.join(random.choices(string.punctuation, k=10))
        invalid_ids = []
        invalid_ids = [random_string, random_number, random_symbols]
        self.log.info("Printing list of invalid storage set ids: %s", invalid_ids)
        self.log.info("Step 1: Send GET request with invalid storage ID")
        for ids in invalid_ids:
            self.log.info("Sending request for: %s", ids)
            resp = self.csm_obj.get_storage_topology(storage_set_id = str(ids))
            assert resp.status_code == HTTPStatus.NOT_FOUND, \
                            "Status code check failed for get storage topology"
            result, err_msg = self.csm_obj.verify_error_message(resp, resp_error_code, resp_msg_id,
                                                    resp_msg_index)
            assert result, err_msg
        self.log.info("##### Test ended -  %s #####", test_case_name)

    @pytest.mark.lc
    @pytest.mark.csmrest
    @pytest.mark.cluster_user_ops
    @pytest.mark.tags('TEST-45678')
    def test_45678(self):
        """
        Verify GET cluster topology with invalid node id
        """
        test_case_name = cortxlogging.get_frame()
        self.log.info("##### Test started -  %s #####", test_case_name)
        test_cfg = self.csm_conf["test_45678"]
        resp_error_code = test_cfg["error_code"]
        resp_msg_id = test_cfg["message_id"]
        resp_msg_index = test_cfg["message_index"]
        random_string = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
        random_number = self.csm_obj.random_gen.randrange(1, 99999)
        random_symbols = ''.join(random.choices(string.punctuation, k=10))
        invalid_ids = []
        invalid_ids = [random_string, random_number, random_symbols]
        self.log.info("Printing list of invalid node ids: %s", invalid_ids)
        self.log.info("Step 1: Send GET request with invalid storage ID")
        for ids in invalid_ids:
            self.log.info("Sending request for: %s", ids)
            resp = self.csm_obj.get_node_topology(node_id = str(ids))
            assert resp.status_code == HTTPStatus.NOT_FOUND, \
                            "Status code check failed for get node topology"
            resp, err_msg = self.csm_obj.verify_error_message(resp, resp_error_code, resp_msg_id,
                                                    resp_msg_index)
            assert resp, err_msg
        self.log.info("##### Test ended -  %s #####", test_case_name)

    #Test not ready
    @pytest.mark.lc
    @pytest.mark.csmrest
    @pytest.mark.cluster_user_ops
    @pytest.mark.tags('TEST-45677')
    def test_45677(self):
        """
        Verify GET cluster topology with valid node id
        """
        test_case_name = cortxlogging.get_frame()
        self.log.info("##### Test started -  %s #####", test_case_name)
        node_id_list = []
        get_topology = self.csm_obj.get_system_topology()
        self.log.info("Step 1: Send node details query request")
        resp = self.csm_obj.get_node_topology()
        assert resp.status_code == HTTPStatus.OK, \
						   "Status code check failed for get node topology"
        self.log.info("Step 2: Send same request with node id")
        for ids in get_topology["topology"]["nodes"].keys():
            if 'id' in ids:
                node_id_list.append(ids)
        for node_ids in node_id_list:
            resp = self.csm_obj.get_node_topology(node_id = node_ids)
            assert resp.status_code == HTTPStatus.OK, \
						   "Status code check failed for get node topology"
            self.log.info("Verify only one node is present in response")
        self.log.info("Get pod names")
        pod_list = self.csm_obj.master.get_all_pods(pod_prefix=POD_NAME_PREFIX)
        for pod_name in pod_list[1]:
            self.log.info(" Step 3: login to each pod and get machine-id")
            resp = self.csm_obj.master.get_machine_id_for_pod(pod_name)
            for node_id in node_id_list:
                assert node_id == resp[1], "Machine id mismatch found"
            self.log.info("Step 4: login to each pod and check hostname")
            resp = self.csm_obj.master.get_pod_hostname(pod_name=pod_name)
            resp_node = get_topology["topology"]["nodes"]
            for hostnames in resp_node:
                assert hostnames["hostname"] == resp[1], "Hostname mismatch found"
            self.log.info("Step 5: Check services list")
            self.log.info("Step 6: Check component list")
        self.log.info("##### Test ended -  %s #####", test_case_name)
