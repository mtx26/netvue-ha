//go:build !linux && !windows

package manager

import "errors"

func protectedFreeBytes(string) (uint64, error) {
	return 0, errors.New("protected storage space checks are unsupported on this platform")
}

func syncProtectedDirectory(string) error {
	return errors.New("protected storage durability is unsupported on this platform")
}
