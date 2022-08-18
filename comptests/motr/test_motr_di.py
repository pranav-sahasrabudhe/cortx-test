# -*- coding: utf-8 -*-
#
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

"""
Module is intended to cater Motr level DI tests which utilize M0* utils and validate data
corruption detection. It will host all test classes or functions related to detection of
discrepancies in data blocks, checksum, parity and emaps.

m0cp -G -l inet:tcp:cortx-client-headless-svc-ssc-vm-rhev4-2620@21201
-H inet:tcp:cortx-client-headless-svc-ssc-vm-rhev4-2620@22001
-p 0x7000000000000001:0x110 -P 0x7200000000000001:0xae

m0cp from data unit aligned offset 0
-s 4096 -c 10 -o 1048583 /root/infile -L 3
-s 4096 -c 1 -o 1048583 /root/myfile -L 3 -u -O 0
m0cat   -o 1048583 -s 4096 -c 10 -L 3 /root/dest_myfile

2) m0cp from data unit aligned offset 16384
m0cp  -s 4096 -c 10 -o 1048584 /root/myfile -L 3
m0cat   -o 1048584 -s 4096 -c 10 -L 3 /root/dest_myfile
m0cp  -s 4096 -c 1 -o 1048584 /root/myfile -L 3 -u -O 16384
m0cat   -o 1048584 -s 4096 -c 10 -L 3 /root/dest_myfile
m0cp  -s 4096 -c 4 -o 1048584 /root/myfile -L 3 -u -O 16384
m0cat   -o 1048584 -s 4096 -c 10 -L 3 /root/dest_myfile
3) m0cp from non aligned offset 4096
m0cp  -s 4096 -c 10 -o 1048587 /root/myfile -L 3
m0cat -o 1048587 -s 4096 -c 10 -L 3 /root/dest_myfile
m0cp  -s 4096 -c 4 -o 1048587 /root/myfile -L 3 -u -O 4096
m0cat -o 1048587 -s 4096 -c 10 -L 3 /root/dest_myfile

"""

import os
import csv
import logging
import secrets
from builtins import list

import pytest

from commons.helpers.pods_helper import LogicalNode
from libs.motr import TEMP_PATH
from libs.motr.motr_core_k8s_lib import MotrCoreK8s
from libs.motr.emap_fi_adapter import MotrCorruptionAdapter
from libs.dtm.dtm_recovery import DTMRecoveryTestLib
from commons.utils import assert_utils
from commons.helpers.health_helper import Health
from config import CMN_CFG
from commons.params import MOTR_DI_ERR_INJ_LOCAL_PATH

logger = logging.getLogger(__name__)


@pytest.fixture(scope="class", autouse=False)
def setup_teardown_fixture(request):
    """
    Yield fixture to setup pre requisites and teardown them.
    Part before yield will be invoked prior to each test case and
    part after yield will be invoked after test call i.e as teardown.
    """
    request.cls.log = logging.getLogger(__name__)
    request.cls.log.info("STARTED: Setup test operations.")
    request.cls.nodes = CMN_CFG["nodes"]
    request.cls.m0crate_workload_yaml = os.path.join(os.getcwd(), "config/motr/sample_m0crate.yaml")
    request.cls.m0crate_test_csv = os.path.join(os.getcwd(), "config/motr/m0crate_tests.csv")
    with open(request.cls.m0crate_test_csv) as csv_fh:
        request.cls.csv_data = [row for row in csv.DictReader(csv_fh)]
    request.cls.log.info("ENDED: Setup test suite operations.")
    yield
    request.cls.log.info("STARTED: Test suite Teardown operations")
    request.cls.log.info("ENDED: Test suite Teardown operations")


class TestCorruptDataDetection:
    """Test suite aimed at verifying detection of data corruption in degraded mode.
    Detection supported for following entities in Normal and degraded mode.
    1. Checksum
    2. Data blocks
    3. Parity
    """

    worker_node_list = None
    master_node_list = None
    passwd = None
    uname = None
    hostname = None
    test_node = None
    node_num = None
    list1 = None
    node_cnt = None

    @classmethod
    def setup_class(cls):
        """Setup class for running Motr tests"""
        logger.info("STARTED: Setup Operation")
        cls.motr_obj = MotrCoreK8s()
        cls.emap_adapter_obj = MotrCorruptionAdapter(CMN_CFG, oid="1234:1234")
        cls.dtm_obj = DTMRecoveryTestLib(max_attempts=0)
        cls.master_node_list = list()
        cls.worker_node_list = list()

        for node in CMN_CFG["nodes"]:
            node_obj = LogicalNode(
                hostname=node["hostname"], username=node["username"], password=node["password"]
            )

            if node["node_type"].lower() == "master":
                cls.master_node_list.append(node_obj)
            else:
                cls.worker_node_list.append(node_obj)

            cls.health_obj = Health(
                hostname=node["hostname"], username=node["username"], password=node["password"]
            )
        cls.m0d_process = "m0d"
        cls.system_random = secrets.SystemRandom()
        logger.info("ENDED: Setup Operation")

    def teardown_class(self):
        """Teardown Node object"""
        self.motr_obj.close_connections()
        del self.motr_obj

    # pylint: disable=R0914
    def m0cp_corrupt_data_m0cat(self, layout_ids, bsize_list, count_list, offsets):
        """
        Create an object with M0CP, corrupt with M0CP and
        validate the corruption with md5sum after M0CAT.
        """
        logger.info("STARTED: m0cp, corrupt and m0cat workflow")
        infile = TEMP_PATH + "input"
        outfile = TEMP_PATH + "output"
        node_pod_dict = self.motr_obj.get_node_pod_dict()
        motr_client_num = self.motr_obj.get_number_of_motr_clients()
        object_id = (
            str(self.system_random.randint(1, 1024 * 1024))
            + ":"
            + str(self.system_random.randint(1, 1024 * 1024))
        )
        for client_num in range(motr_client_num):
            for node in node_pod_dict:

                for b_size, (cnt_c, cnt_u), layout, offset in zip(
                    bsize_list, count_list, layout_ids, offsets
                ):
                    self.motr_obj.dd_cmd(b_size, cnt_c, infile, node)
                    self.motr_obj.cp_cmd(b_size, cnt_c, object_id, layout, infile, node, client_num)
                    self.motr_obj.cat_cmd(
                        b_size, cnt_c, object_id, layout, outfile, node, client_num
                    )
                    self.motr_obj.cp_update_cmd(
                        b_size=b_size,
                        count=cnt_u,
                        obj=object_id,
                        layout=layout,
                        file=infile,
                        node=node,
                        client_num=client_num,
                        offset=offset,
                    )
                    self.motr_obj.cat_cmd(
                        b_size, cnt_c, object_id, layout, outfile, node, client_num
                    )
                    self.motr_obj.md5sum_cmd(infile, outfile, node, flag=True)
                    self.motr_obj.unlink_cmd(object_id, layout, node, client_num)

            logger.info("Stop: Verify multiple m0cp/cat operation")

    # pylint: disable=R0914
    # Todo: make the fn similar to motr_inject_checksum_corruption
    #  OR have parameter to the function and use same for parity and data
    def m0cp_corrupt_parity_m0cat(self, layout_ids, bsize_list, count_list, offsets) -> bool:
        """
        Create an object with M0CP,
        Identify the emap blocks corresponding to parity blocks
        and corrupt single parity block's CKSUM with emap script and
        validate the corruption M0CAT.
        """
        logger.info("STARTED: m0cat workflow")
        infile = kwargs.get("infile", TEMP_PATH + "input")
        outfile = kwargs.get("outfile", TEMP_PATH + "output")
        client_num = kwargs.get("client_num", 1)
        node_pod_dict = self.motr_obj.get_node_pod_dict()
        motr_client_num = self.motr_obj.get_number_of_motr_clients()
        object_id = (
            str(self.system_random.randint(1, 1024 * 1024))
            + ":"
            + str(self.system_random.randint(1, 1024 * 1024))
        )
        for client_num in range(motr_client_num):
            for node in node_pod_dict:
                for b_size, (cnt_c, cnt_u), layout, offset in zip(
                    bsize_list, count_list, layout_ids, offsets
                ):
                    self.motr_obj.dd_cmd(b_size, cnt_c, infile, node)
                    self.motr_obj.cp_cmd(b_size, cnt_c, object_id, layout, infile, node, client_num)
                    self.motr_obj.cat_cmd(
                        b_size, cnt_c, object_id, layout, outfile, node, client_num
                    )
                    # Todo: Find the metadata device from solution.yaml - this is softlink of
                    # /etc/cortx/motr/m0d-xxxxx/db/o/10000000000:\2a something
                    # This is required as paramater -m to error_injection script

                    # Todo: need to restart m0tr container for taking emap effect

                    # self.motr_obj.cp_update_cmd(
                    #     b_size=b_size,
                    #     count=cnt_u,
                    #     obj=object_id,
                    #     layout=layout,
                    #     file=infile,
                    #     node=node,
                    #     client_num=client_num,
                    #     offset=offset,
                    # )
                    self.motr_obj.cat_cmd(
                        b_size, cnt_c, object_id, layout, outfile, node, client_num
                    )
                    self.motr_obj.md5sum_cmd(infile, outfile, node, flag=True)
                    self.motr_obj.unlink_cmd(object_id, layout, node, client_num)

            logger.info("Stop: Verify multiple m0cp/cat operation")
        return True  # Todo: return status to be worked as per responses

    # pylint: disable=R0914
    def motr_inject_checksum_corruption(self, layout_ids, bsize_list, count_list, offsets):
        """
        Create an object with M0CP, identify the emap blocks corresponding to data blocks
        and corrupt single parity block with emap script and
        validate the corruption M0CAT.
        """
        logger.info("STARTED: Emap corruption workflow")
        infile = TEMP_PATH + "input"
        outfile = TEMP_PATH + "output"
        node_pod_dict = self.motr_obj.get_node_pod_dict()
        object_id_list = []

        # ON THE DATA POD: ==========>>>>>>

        # Copy the emap script to controller node's root dir
        err_inj_script_path = str(MOTR_DI_ERR_INJ_LOCAL_PATH)
        copy_status, resp = self.motr_obj.master_node_list[0].copy_file_to_remote(
            err_inj_script_path, const.MOTR_DI_ERR_INJ_SCRIPT_PATH
        )
        if not copy_status:
            return copy_status, resp
        else:
            logger.debug(f"Error Injection Script File already exists...")

        remote_script_path = const.CONTAINER_PATH
        motr_container_name = f"{const.MOTR_CONTAINER_PREFIX}-001"

        # For all pods in the system
        for node_pod in node_pod_dict:

            # Format the Object ID is xxx:yyy format
            object_id = (
                str(self.system_random.randint(1, 1024 * 1024))
                + ":"
                + str(self.system_random.randint(1, 1024 * 1024))
            )

            for b_size, (cnt_c, cnt_u), layout, offset in zip(
                bsize_list, count_list, layout_ids, offsets
            ):
                # On the Client POD - cortx - hax container ==========>>>>>>
                # Create file (object) with dd
                self.motr_obj.dd_cmd(b_size, cnt_c, infile, node_pod)
                # Create object
                object_id_list.append(object_id)  # Store object_id for future delete
                self.motr_obj.cp_cmd(
                    b_size, cnt_c, object_id, layout, infile, node_pod, 0
                )  # client_num

                logger.debug(f"object_id_list is: ###### {object_id_list}")

        logger.info(f"Copying the error injection script to cortx_motr_io containers in data pods.")
        pod_list = self.motr_obj.node_obj.get_all_pods(const.POD_NAME_PREFIX)
        for pod in pod_list:
            result = self.motr_obj.master_node_list[0].copy_file_to_container(
                const.MOTR_DI_ERR_INJ_SCRIPT_PATH, pod, remote_script_path, motr_container_name
            )

            if not result:
                raise FileNotFoundError

        # Run Emap on all objects
        self.emap_adapter_obj.inject_checksum_corruption(object_id_list)

        # Todo
        # self.motr_obj.dump_m0trace_log(filepath=, node=)
        # tfid_dict = self.motr_obj.read_m0trace_log(filepath=)

        # Todo: need to restart m0tr container for taking emap effect

        for index, node_pod in enumerate(node_pod_dict):
            for b_size, (cnt_c, cnt_u), layout, offset in zip(
                bsize_list, count_list, layout_ids, offsets
            ):
                # On the Client POD - cortx - hax container ==========>>>>>>

                # # Read objects after
                self.motr_obj.cat_cmd(
                    b_size, cnt_c, object_id_list[index], layout, outfile, node_pod, 0, di_g=True
                )

                self.motr_obj.md5sum_cmd(infile, outfile, node_pod, flag=True)

                self.motr_obj.unlink_cmd(object_id_list[index], layout, node_pod, 0)

            logger.info("Stop: Verify emap corruption detection operation")

        return True  # Todo: return status to be worked as per responses

    def m0cat_md5sum_m0unlink(self, bsize_list, count_list, layout_ids, object_list, **kwargs):
        """
        Validate the corruption with md5sum after M0CAT and unlink the object
        """
        logger.info("STARTED: m0cat workflow")
        infile = kwargs.get("infile", TEMP_PATH + "input")
        outfile = kwargs.get("outfile", TEMP_PATH + "output")
        client_num = kwargs.get("client_num", 1)
        node_pod_dict = self.motr_obj.get_node_pod_dict()
        for node in node_pod_dict:
            for b_size, cnt_c, layout, obj_id in zip(
                bsize_list, count_list, layout_ids, object_list
            ):
                self.motr_obj.cat_cmd(b_size, cnt_c, obj_id, layout, outfile, node, client_num)
                # Verify the md5sum
                self.motr_obj.md5sum_cmd(infile, outfile, node, flag=True)
                # Delete the object
                self.motr_obj.unlink_cmd(obj_id, layout, node, client_num)
                logger.info("Stop: Verify m0cat operation")

    @pytest.mark.skip(reason="Feature Unavailable")
    @pytest.mark.tags("TEST-41742")
    @pytest.mark.motr_di
    def test_corrupt_checksum_emap_aligned(self):
        """
        Checksum corruption and detection with EMAP/m0cp and m0cat
        Copy motr block with m0cp and corrupt/update with m0cp and then
        Corrupt checksum block using m0cp+error_injection.py script
        Read from object with m0cat should throw an error.
        -s 4096 -c 10 -o 1048583 /root/infile -L 3
        -s 4096 -c 1 -o 1048583 /root/myfile -L 3 -u -O 0
        -o 1048583 -s 4096 -c 10 -L 3 /root/dest_myfile
        """
        count_list = [["4", "1"]]
        bsize_list = ["1M"]
        layout_ids = ["9"]
        offsets = [0]

        self.motr_inject_checksum_corruption(layout_ids, bsize_list, count_list, offsets)

    @pytest.mark.tags("TEST-41739")
    @pytest.mark.motr_di
    def test_m0cp_m0cat_block_corruption(self):
        """
        Corrupt data block using m0cp and reading from object with m0cat should error.
        -s 4096 -c 10 -o 1048583 /root/infile -L 3
        -s 4096 -c 1 -o 1048583 /root/myfile -L 3 -u -O 0
        -o 1048583 -s 4096 -c 10 -L 3 /root/dest_myfile
        """
        count_list = [["10", "1"], ["10", "1"]]
        bsize_list = ["4K", "4K"]
        layout_ids = ["3", "3"]
        offsets = [0, 16384]
        self.m0cp_corrupt_data_m0cat(layout_ids, bsize_list, count_list, offsets)

    @pytest.mark.skip(reason="Test incomplete without teardown")
    @pytest.mark.tags("TEST-41766")
    @pytest.mark.motr_di
    def test_m0cp_m0cat_block_corruption_degraded_mode(self):
        """
        In degraded mode Corrupt data block using m0cp and reading
        from object with m0cat should error.
        """
        logger.info(
            "Step 1: Shutdown random data pod by making replicas=0 and "
            "verify cluster & remaining pods status"
        )
        self.motr_obj.switch_cluster_to_degraded_mode()
        count_list = [["10", "1"], ["10", "1"]]
        bsize_list = ["4K", "4K"]
        layout_ids = ["3", "3"]
        offsets = [0, 16384]
        self.m0cp_corrupt_data_m0cat(layout_ids, bsize_list, count_list, offsets)

    @pytest.mark.tags("TEST-41911")
    @pytest.mark.motr_di
    def test_m0cp_m0cat_block_corruption_unaligned(self):
        """
        Corrupt data block using m0cp and reading from object with m0cat should error.
        -s 4096 -c 10 -o 1048583 /root/infile -L 3
        -s 4096 -c 1 -o 1048583 /root/myfile -L 3 -u -O 0
        -o 1048583 -s 4096 -c 10 -L 3 /root/dest_myfile
        """
        count_list = [["10", "10"]]
        bsize_list = ["4K"]
        layout_ids = ["3"]
        offsets = [4096]
        self.m0cp_corrupt_data_m0cat(layout_ids, bsize_list, count_list, offsets)

    # @pytest.mark.skip(reason="Feature Unavailable")
    @pytest.mark.tags("TEST-41742")
    @pytest.mark.motr_di
    def test_corrupt_checksum_emap_aligned(self):
        """
        Checksum corruption and detection with EMAP/m0cp and m0cat
        Copy motr block with m0cp and corrupt/update with m0cp and then
        Corrupt checksum block using m0cp+error_injection.py script
        Read from object with m0cat should throw an error.
        -s 4096 -c 10 -o 1048583 /root/infile -L 3
        -s 4096 -c 1 -o 1048583 /root/myfile -L 3 -u -O 0
        -o 1048583 -s 4096 -c 10 -L 3 /root/dest_myfile
        """
        count_list = [["4", "1"]]
        bsize_list = ["4k"]
        layout_ids = ["1"]
        offsets = [0]

        self.motr_inject_checksum_corruption(layout_ids, bsize_list, count_list, offsets)

    # @pytest.mark.skip(reason="Feature Unavailable")
    @pytest.mark.tags("TEST-41768")
    @pytest.mark.motr_di
    def test_corrupt_parity_degraded_aligned(self):
        """
        Degraded Mode: Parity corruption and detection with M0cp and M0cat
        Bring the setup in degraded mode by restating m0d with delay
        and then follow next steps:
        Copy motr object with m0cp
        Identify parity block using m0trace logs created during m0cp
        Corrupt parity block using m0cp+error_injection.py script
        Read from object with m0cat should throw an error.
        -s 4096 -c 10 -o 1048583 /root/infile -L 3
        -s 4096 -c 1 -o 1048583 /root/myfile -L 3 -u -O 0
        -o 1048583 -s 4096 -c 10 -L 3 /root/dest_myfile
        """
        count_list = [["8", "4"]]
        bsize_list = ["1M"]
        layout_ids = ["9"]
        offsets = [0]
        # Check for deployment status using kubectl commands - Taken care in setup stage
        # Todo: Invoke in degraded mode depends on PR 1732
        # Todo: Find parity block and corrupt
        #
        logger.info("STARTED: Test Parity corruption in degraded mode - aligned")
        test_prefix = "test-41768"

        logger.info("Step 1: Perform Single m0d Process Restart")
        resp = self.dtm_obj.process_restart_with_delay(
            master_node=self.master_node_list[0],
            health_obj=self.health_obj,
            pod_prefix=const.POD_NAME_PREFIX,
            container_prefix=const.MOTR_CONTAINER_PREFIX,
            process=self.m0d_process,
            check_proc_state=True,
        )
        assert_utils.assert_true(resp, "Failure observed during process restart/recovery")
        logger.info("Step 1: m0d restarted and recovered successfully")

        logger.info("Step 2: Perform m0cp and corrupt the parity block")
        resp = self.motr_inject_checksum_corruption(layout_ids, bsize_list, count_list, offsets)
        assert_utils.assert_true(resp)
        logger.info("Step 2: Successfully performed m0cp and corrupt the parity block")
        logger.info(f"ENDED:{test_prefix} Test Parity corruption in degraded mode - aligned")

    @pytest.mark.skip(reason="Feature Unavailable")
    @pytest.mark.tags("TEST-45162")
    @pytest.mark.motr_di
    def test_corrupt_data_all_du_unaligned(self):
        """
        Corrupt each data unit one by one and check Motr is able to detect read error 4KB IO
        with 4KB Unit Size and N=4 K=2 aligned data blocks
        In the loop for each data unit,
        Copy motr object with m0cp
        Read from object with m0cat should throw an error.
        -s 4k -c 4 -o 1234:1234 /root/infile -L 1
        -s 4k -c 4 -o 1234:1234 /root/myfile -L 1 -u -O 0
        -o 1234:1234 -s 4k -c 4 -L 1 /root/dest_myfile
        """
        count_list = [["4", "4"]]
        bsize_list = ["4096"]
        layout_ids = ["1"]
        offsets = [0]
        test_prefix = "test-45162"
        logger.info("STARTED: Test data unit corruption in loop - aligned")

        # Todo: Run the following 4 times for 4 data units after identifying nodes on which
        #  those are stored
        for _ in range(4):
            logger.info("Step 1: Perform m0cp and corrupt the data block")
            self.m0cp_corrupt_data_m0cat(layout_ids, bsize_list, count_list, offsets)
            logger.info("Successfully performed m0cp and corrupt the data block")
        logger.info(f"ENDED:{test_prefix} Test Parity corruption in degraded mode - aligned")

    @pytest.mark.tags("TEST-45716")
    @pytest.mark.motr_di
    def test_data_block_corruption_one_by_one(self):
        """
        Corrupt data block one by one using emap script and
         reading from object with m0cat should error.
        -s 4096 -c 10 -o 1048583 /root/infile -L 1
        -s 4096 -c 1 -o 1048583 /root/myfile -L 1 -u -O 0
        -o 1048583 -s 4096 -c 10 -L 1 /root/dest_myfile
        """
        count_list = ["10"]
        bsize_list = ["4K"]
        layout_ids = ["1"]
        logger.info("STARTED: m0cp, corrupt and m0cat workflow of " "each Data block one by one")
        infile = TEMP_PATH + "input"
        outfile = TEMP_PATH + "output"
        node_pod_dict = self.motr_obj.get_node_pod_dict()
        motr_client_num = self.motr_obj.get_number_of_motr_clients()
        object_list = []
        fid_resp = {}
        for client_num in range(motr_client_num):
            for node in node_pod_dict:
                object_id = (
                    str(self.system_random.randint(1, 1024 * 1024))
                    + ":"
                    + str(self.system_random.randint(1, 1024 * 1024))
                )
                for (
                    b_size,
                    cnt_c,
                    layout,
                ) in zip(bsize_list, count_list, layout_ids):
                    self.motr_obj.dd_cmd(b_size, cnt_c, infile, node)
                    # Add object id in a list
                    object_list.append(object_id)
                    self.motr_obj.cp_cmd(
                        b_size, cnt_c, object_id, layout, infile, node, client_num, di_g=True
                    )
                    self.motr_obj.cat_cmd(
                        b_size, cnt_c, object_id, layout, outfile, node, client_num
                    )
                filepath = self.motr_obj.dump_m0trace_log(f"{node}-trace_log.txt", node)
                logger.debug("filepath is %s", filepath)
                # Fetch the FID from m0trace log
                fid_resp = self.motr_obj.read_m0trace_log(filepath)
                logger.debug("fid_resp is %s", fid_resp)
            metadata_path = self.emap_adapter_obj.get_metadata_device(
                self.motr_obj.master_node_list[0]
            )
            logger.debug("metadata device is %s", metadata_path[0])
            data_gob_id_resp = self.emap_adapter_obj.get_object_gob_id(
                metadata_path[0], fid=fid_resp
            )
            logger.debug("metadata device is %s", data_gob_id_resp)
            # Corrupt the data block 1
            for fid in data_gob_id_resp:
                corrupt_data_resp = self.emap_adapter_obj.inject_fault_k8s(fid)
                if not corrupt_data_resp:
                    logger.debug("Failed to corrupt the block %s", fid)
                assert_utils.assert_true(corrupt_data_resp)
            # Read the data using m0cp utility
            self.m0cat_md5sum_m0unlink(
                bsize_list, count_list, layout_ids, object_list, client_num=client_num
            )
