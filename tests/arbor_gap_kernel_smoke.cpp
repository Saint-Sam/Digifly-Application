#include <arbor/mechanism_abi.h>

#include <algorithm>
#include <cmath>
#include <iostream>
#include <string>
#include <vector>

extern "C" {
arb_mechanism_interface* make_arb_digifly_gap_catalogue_gap_interface_multicore();
arb_mechanism_interface* make_arb_digifly_gap_catalogue_rect_gap_interface_multicore();
arb_mechanism_interface* make_arb_digifly_gap_catalogue_hetero_rect_gap_interface_multicore();
}

namespace {

bool close(double actual, double expected, double tolerance = 1e-11) {
    return std::abs(actual - expected) <= tolerance;
}

int fail(const std::string& message) {
    std::cerr << message << '\n';
    return 1;
}

struct endpoint_pack {
    explicit endpoint_pack(unsigned width, unsigned parameter_count, unsigned state_count):
        width(std::max(2u, width)),
        voltage(this->width, -65),
        current(this->width, 0),
        conductivity(this->width, 0),
        node_index(this->width),
        peer_index(this->width),
        weight(this->width, 0),
        parameter_storage(parameter_count, std::vector<double>(this->width, 0)),
        state_storage(state_count, std::vector<double>(this->width, 0)),
        parameter_ptrs(parameter_count),
        state_ptrs(state_count) {
        for (unsigned index = 0; index < this->width; ++index) {
            node_index[index] = index;
            peer_index[index] = index;
        }
        peer_index[0] = 1;
        peer_index[1] = 0;
        weight[0] = 1;
        weight[1] = 1;
        for (unsigned index = 0; index < parameter_count; ++index) {
            parameter_ptrs[index] = parameter_storage[index].data();
        }
        for (unsigned index = 0; index < state_count; ++index) {
            state_ptrs[index] = state_storage[index].data();
        }

        constraints.n_none = 1;
        constraints.none = &first_partition;

        pack.width = this->width;
        pack.dt = 0.1;
        pack.vec_v = voltage.data();
        pack.vec_i = current.data();
        pack.vec_g = conductivity.data();
        pack.node_index = node_index.data();
        pack.peer_index = peer_index.data();
        pack.weight = weight.data();
        pack.index_constraints = constraints;
        pack.parameters = parameter_ptrs.empty() ? nullptr : parameter_ptrs.data();
        pack.state_vars = state_ptrs.empty() ? nullptr : state_ptrs.data();
    }

    void clear_accumulators() {
        std::fill(current.begin(), current.end(), 0);
        std::fill(conductivity.begin(), conductivity.end(), 0);
    }

    unsigned width;
    std::vector<double> voltage;
    std::vector<double> current;
    std::vector<double> conductivity;
    std::vector<arb_index_type> node_index;
    std::vector<arb_index_type> peer_index;
    std::vector<double> weight;
    std::vector<std::vector<double>> parameter_storage;
    std::vector<std::vector<double>> state_storage;
    std::vector<double*> parameter_ptrs;
    std::vector<double*> state_ptrs;
    arb_index_type first_partition = 0;
    arb_constraint_partition constraints = {};
    arb_mechanism_ppack pack = {};
};

void set_endpoints(std::vector<double>& values, double endpoint_0, double endpoint_1) {
    values[0] = endpoint_0;
    values[1] = endpoint_1;
}

} // namespace

int main() {
    {
        auto* interface = make_arb_digifly_gap_catalogue_gap_interface_multicore();
        endpoint_pack endpoints(interface->partition_width, 1, 0);
        set_endpoints(endpoints.voltage, -60, -40);
        set_endpoints(endpoints.parameter_storage[0], 0.003, 0.003);
        interface->init_mechanism(&endpoints.pack);
        interface->compute_currents(&endpoints.pack);
        if (!close(endpoints.current[0], -0.06) || !close(endpoints.current[1], 0.06)) {
            return fail("Ohmic gap current or uS*mV-to-nA unit behavior is wrong.");
        }
    }

    {
        auto* interface = make_arb_digifly_gap_catalogue_rect_gap_interface_multicore();
        endpoint_pack endpoints(interface->partition_width, 1, 1);
        set_endpoints(endpoints.voltage, -60, -40);
        set_endpoints(endpoints.parameter_storage[0], 0.003, 0.003);
        interface->init_mechanism(&endpoints.pack);
        interface->compute_currents(&endpoints.pack);
        if (!close(endpoints.current[0], -0.06) || !close(endpoints.current[1], 0)) {
            return fail("Hard rectifier did not conduct only when v_peer > v.");
        }
    }

    {
        auto* interface = make_arb_digifly_gap_catalogue_hetero_rect_gap_interface_multicore();
        endpoint_pack endpoints(interface->partition_width, 8, 6);
        set_endpoints(endpoints.voltage, -60, -40);
        set_endpoints(endpoints.parameter_storage[0], 0.005, 0.005); // gmax_open
        set_endpoints(endpoints.parameter_storage[1], 0, 0);         // gmax_closed
        set_endpoints(endpoints.parameter_storage[2], 1, -1);        // paired orientation
        set_endpoints(endpoints.parameter_storage[3], 0, 0);         // vhalf
        set_endpoints(endpoints.parameter_storage[4], 5, 5);         // vslope
        set_endpoints(endpoints.parameter_storage[5], 0.20, 0.20);   // residual floor
        set_endpoints(endpoints.parameter_storage[6], 6, 6);         // tau open
        set_endpoints(endpoints.parameter_storage[7], 2, 2);         // tau close

        interface->init_mechanism(&endpoints.pack);
        endpoints.state_storage[0][0] = 0;
        endpoints.state_storage[0][1] = 0;
        interface->compute_currents(&endpoints.pack);
        if (!close(endpoints.state_storage[1][0], 0.001) ||
            !close(endpoints.state_storage[1][1], 0.001)) {
            return fail("Heterotypic rectifier did not enforce the empirical residual floor.");
        }
        if (!close(endpoints.current[0], -0.02) || !close(endpoints.current[1], 0.02)) {
            return fail("Heterotypic residual-floor current is not equal and opposite.");
        }

        endpoints.pack.dt = 0.5;
        set_endpoints(endpoints.voltage, -40, -60);
        endpoints.state_storage[0][0] = 0.5;
        endpoints.state_storage[0][1] = 0.5;
        interface->advance_state(&endpoints.pack);
        const double opened = endpoints.state_storage[0][0];

        set_endpoints(endpoints.voltage, -60, -40);
        endpoints.state_storage[0][0] = 0.5;
        endpoints.state_storage[0][1] = 0.5;
        interface->advance_state(&endpoints.pack);
        const double closed = endpoints.state_storage[0][0];

        if (!(opened > 0.5 && closed < 0.5)) {
            return fail("Heterotypic gate did not open and close with oriented peer voltage.");
        }
        if (!(0.5 - closed > opened - 0.5)) {
            return fail("tau_close_ms=2 did not close faster than tau_open_ms=6 opens.");
        }
    }

    std::cout << "Arbor gap kernels passed two-endpoint behavior checks.\n";
    return 0;
}
