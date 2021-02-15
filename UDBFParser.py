from UDBFData import UDBFHeader
from datetime import datetime, timedelta
import numpy as np
import os
import struct


class BinaryReader(object):
    """
    Helper class to read binary UDBF data.
    """

    def __init__(self, data: bytes):
        self._data = data
        self._current_pointer = 0

        endian_prefix_dict = {0: "<", 1: ">"}

        endian_prefix_byte = self.unpack("B", 1)

        try:
            self.endian_prefix = endian_prefix_dict[endian_prefix_byte]
        except Exception as e:
            exit_state = "Cannot interpret the endian "
            exit_state += "prefix value: " + str(endian_prefix_byte)
            raise RuntimeError(exit_state + ": " + str(e))

    def move_pointer_to(self, new_pointer):
        self._current_pointer = new_pointer

    @property
    def current_pointer(self):
        return self._current_pointer

    def unpack(self, data_type, n_bytes):

        if self.current_pointer == 0:
            endian_prefix = ""
        else:
            endian_prefix = self.endian_prefix

        start = self.current_pointer
        end = self.current_pointer + n_bytes

        data_type = endian_prefix + data_type
        try:
            value = struct.unpack(data_type, self._data[start:end])[0]
        except Exception as e:
            raise RuntimeError("Cannot interpret file in UDBF. " + str(e))

        self._current_pointer += n_bytes

        return value

    def read_byte_string(self, length):

        string = []
        for i in range(length):
            field = self.unpack("c", 1)
            field = field.decode("UTF-8", errors="strict")
            field = field.rstrip("\x00")
            string.append(str(field))

        return "".join(string)

    def __len__(self):

        return len(self._data)


class BinaryFileReader(BinaryReader):
    """
    Implements BinaryReader for a file (infile).
    """

    def __init__(self, infile: str):

        if os.path.isfile(infile) is False:
            raise OSError(infile + " not found")

        with open(infile, mode="rb") as data_file:
            data = data_file.read()

        super().__init__(data)


class UDBFParser(object):
    """Parses binary data according to the UDBF standard. The binary data 
    must be provided by the BinaryReader given in the constructor.

    Attributes:
    header: UDBFHeader
        Meta information.

    Methods:
    signal(channel): (timestamps: list, values: list)
        Time series for the specified channel.
    """

    def __init__(self, reader: BinaryReader, sampling_rate_unit="Hz"):

        self._reader = reader

        self._ole_time_zero = datetime(1899, 12, 30, 0, 0, 0)
        self.variable_type_conversion = {1: ("?", 1), 8: ("f", 4)}

        self.version = self._reader.unpack("H", 2)
        self.vendor_length = self._reader.unpack("H", 2)
        self.vendor = self._reader.read_byte_string(self.vendor_length)
        self.with_checksum = self._reader.unpack("B", 1)
        self.n_additional_modules = self._reader.unpack("H", 2)

        if self.n_additional_modules != 0:
            self.module_type = self._reader.unpack("H", 2)
            self.module_additional_data_struct = self._reader.unpack("H", 2)
            module_string_length = self.n_additional_modules - 4
            module = self._reader.read_byte_string(module_string_length)
            self.additional_module = module

        self.day_factor = self._reader.unpack("d", 8)
        self.time_format = self._reader.unpack("H", 2)
        self.second_factor = self._reader.unpack("d", 8)
        self.header_start_time = self._reader.unpack("d", 8)
        self.sampling_rate = (self._reader.unpack("d", 8),
                              sampling_rate_unit)
        self.number_of_channels = self._reader.unpack("H", 2)

        channel_name_list = []
        channel_direction_list = []
        channel_type_list = []
        channel_unit_list = []
        channel_field_length_list = []
        channel_precision_list = []
        channel_additional_data = []

        for channel in range(self.number_of_channels):

            channel_name_length = self._reader.unpack("H", 2)
            channel_name = self._reader.read_byte_string(channel_name_length)
            channel_name_list.append(channel_name)
            data_direction = self._reader.unpack("H", 2)
            channel_direction_list.append(data_direction)
            data_type = self._reader.unpack("H", 2)
            channel_type_list.append(data_type)
            field_length = self._reader.unpack("H", 2)
            channel_field_length_list.append(field_length)
            precision = self._reader.unpack("H", 2)
            channel_precision_list.append(precision)
            unit_length = self._reader.unpack("H", 2)
            channel_unit = self._reader.read_byte_string(unit_length)
            channel_unit_list.append(channel_unit.strip())
            n_additional_data = self._reader.unpack("H", 2)
            if n_additional_data != 0:
                self._reader.unpack("H", 2)  # additional_data_type
                self._reader.unpack("H", 2)  # additional_data_struct_id

                add = self._reader.read_byte_string(n_additional_data - 4)
                channel_additional_data.append(add)

        self.variable_names = channel_name_list
        self.variable_directions = channel_direction_list
        self.variable_types = channel_type_list
        self.variable_units = channel_unit_list
        self.field_lengths = channel_field_length_list
        self.variable_precision = channel_precision_list
        self.channel_additional_data = channel_additional_data

        self.header_end_byte = self._reader.current_pointer

    @property
    def header(self):

        header_id = "_header"
        if hasattr(self, header_id):
            return getattr(self, header_id)

        header = UDBFHeader(udbf_version=self.version,
                            vendor=self.vendor,
                            sampling_rate=self.sampling_rate,
                            number_of_channels=self.number_of_channels,
                            variable_names=self.variable_names,
                            variable_directions=self.variable_directions,
                            variable_types=self.variable_types,
                            variable_units=self.variable_units,
                            variable_precision=self.variable_precision,
                            channel_additional_data=self.channel_additional_data)

        self._header = header

        return header

    def signal(self, signal_type=np.float32):

        timestamp_id = "timestamps"
        signals_id = "signals"
        if hasattr(self, timestamp_id):
            timestamps = getattr(self, timestamp_id)
            signals = getattr(self, signals_id)
            return (timestamps, signals)

        event_pointer = self._get_signal_start_byte()

        self._reader.move_pointer_to(event_pointer)

        event_length = 8
        for channel in range(self.number_of_channels):
            event_length += self._get_variable_type(channel)[1]

        data_length = len(self._reader)
        n_events = data_length - self._reader.current_pointer
        n_events /= event_length
        n_events = int(n_events-1)
        timestamps = np.ndarray((n_events, ), dtype=datetime)
        signals = np.ndarray((n_events, self.number_of_channels),
                             dtype=signal_type)

        i = 0
        while self._reader.current_pointer + event_length < data_length:
            timestamp = self._reader.unpack("Q", 8)
            timestamp = self._get_time_stamp(timestamp)
            timestamps[i] = timestamp
            event_signal_data = []
            for channel in range(self.number_of_channels):
                variable_type = self._get_variable_type(channel)
                channel_data = self._reader.unpack(variable_type[0],
                                                   variable_type[1])

                event_signal_data.append(channel_data)

            signals[i] = event_signal_data
            i += 1

        del self._reader

        if i != n_events:
            exit_status = "Read number of events ("
            exit_status += str(i) + ") doesn't fit expectation ("
            exit_status += str(n_events) + ")"
            raise RuntimeError(exit_status)

        setattr(self, timestamp_id, timestamps)
        setattr(self, signals_id, signals)

        return (timestamps, signals)

    def _get_time_stamp(self, timestamp):

        seconds_per_day = 60. * 60. * 24.
        day_float = float(timestamp) * self.second_factor
        day_float /= seconds_per_day
        day_float += self.header_start_time * self.day_factor

        return self._ole_time_zero + timedelta(days=day_float)

    def _get_variable_type(self, channel):

        variable_type = self.variable_types[channel]
        return self.variable_type_conversion[variable_type]

    @property
    def sampling_rate(self):
        return self._sampling_rate

    @sampling_rate.setter
    def sampling_rate(self, value):
        if len(value) != 2:
            raise RuntimeError("Sampling rate must have value and unit")

        if value[0] <= 0.:
            raise RuntimeError("Sampling rate not positive")

        self._sampling_rate = (float(value[0]), value[1])

    def _get_signal_start_byte(self):
        """
        From UDBF data sheet:
        8.6.2.2
        Separation Chars:
        There are separation characters inserted. At least 8 pieces and maximal
        as many as needed so that the next valid data byte is written
        to a 16 bytes aligned address.
        """

        header_end_byte = self.header_end_byte
        data_length = len(self._reader)
        if header_end_byte <= 0 or header_end_byte >= data_length:
            info = "Invalid header end byte: " + str(header_end_byte)
            raise RuntimeError(info)

        for i in range(header_end_byte + 8, data_length):
            if i % 16 == 0:
                return i

        raise RuntimeError("Couldn't find signal start byte")