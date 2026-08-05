import errno

import pytest

from runtime import model_output_transaction as transaction


class _InitializationLockedFile:
    def seek(self, *_args):
        raise PermissionError(errno.EACCES, "locked byte range")


def test_lock_initialization_failure_is_reported_as_process_contention():
    with pytest.raises(RuntimeError, match="already preparing this model"):
        transaction._acquire_file_lock(_InitializationLockedFile())
