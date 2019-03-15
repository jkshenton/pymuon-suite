"""
schemas.py

Python schemas for YAML input files for various scripts
"""

# Python 2-to-3 compatibility code
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import yaml
import numpy as np
from schema import Optional, Schema, SchemaError
from scipy.constants import physical_constants as pcnst


def validate_matrix_shape(x):
    x = np.array(x)
    return (x.shape == (1,) or x.shape == (3,) or
            x.shape == (9,) or x.shape == (3, 3))


def validate_supercell(value):
    return (value is None or isinstance(value, int) or
            (validate_matrix_shape(value) and np.array(value).dtype == int))


def validate_all_of(*options):
    def validator(values):
        return all([x.strip() in options for x in values.split(',')])
    return validator


def validate_str(s):
    # Validates a string in a Python 2 and 3 compatible way
    try:
        return isinstance(s, basestring)
    except NameError:
        # It must be Python 3!
        return isinstance(s, str)


def validate_bool(value):
    return (value.strip().lower() in ('true', 't'))


def validate_int3(value):
    v = np.array(value)
    return v.shape == (3,) and v.dtype == int


def validate_int_array(value):
    v = np.array(value)
    return v.dtype == int


def validate_float3(value):
    v = np.array(value)
    return v.shape == (3,) and v.dtype == float


def load_input_file(fname, param_schema, merge=None):
    """Load a given input YAML file and validate it with a schema.
    """

    if merge is None:
        params = yaml.load(open(fname, 'r'))
    else:
        try:
            param_schema.validate(merge)
        except SchemaError as e:
            message = ('Invalid merge params passed to'
                       ' load_input_file\n{0}'.format(e))
            raise RuntimeError(message)
        new_params = yaml.load(open(fname, 'r'))
        params = dict(merge)
        params.update(new_params)

    try:
        params = param_schema.validate(params)
    except SchemaError as e:
        message = 'Invalid input file {0}\n{1}'.format(fname, e)
        raise RuntimeError(message)

    return params


# Parameter file schemas and defaults
MuAirssSchema = Schema({
    # Name to call the folder for containing each structure. This name will
    # be postfixed with a unique number. e.g. struct_001
    Optional('name', default='struct'):
    validate_str,
    # Calculator to generate structure files for. Must be a comma seperated
    # list of values. Currently supported calculators are CASTEP and DFTB+. Can
    # also pass all as an option to generate files for all calculators.
    Optional('calculator', default='dftb+'):
    validate_all_of('castep', 'dftb+', 'all'),
    # Command to use to run CASTEP.
    Optional('castep_command', default='castep.serial'):
    validate_str,
    # Command to use to run DFTB+.
    Optional('dftb_command', default='dftb+'):
    validate_str,
    # File path to the CASTEP parameter file.
    Optional('castep_param', default=None):
    validate_str,
    # The parameter set to use for DFTB+.
    Optional('dftb_set', default='3ob-3-1'):
    validate_str,
    # Whether to turn on periodic boundary conditions in DFTB+
    Optional('dftb_pbc', default=True):
    bool,
    # Radius to use when generating muon sites with the possion disk algorithm.
    Optional('poisson_r', default=0.8):
    float,
    # Van der Waals radius to use when generating muon sites. Default is 0.5.
    Optional('vdw_scale', default=0.5):
    float,
    # Supercell size and shape to use. This can either be a single int, a list
    # of three integers or a 3x3 matrix of integers. For a single number a
    # diagonal matrix will be generated with the integer repeated on the
    # diagonals. For a list of three numbers a diagonal matrix will be
    # generated where the digonal elements are set to the list. A matrix will
    # be used direclty as is. Default is a 3x3 indentity matrix.
    Optional('supercell', default=1):
    validate_supercell,
    # List of three integer k-points. Default is [1,1,1].
    Optional('k_points_grid', default=np.ones(3).astype(int)):
    validate_int3,
    # Name to call the output folder used to store the input files
    # that muon-airss generates.
    Optional('out_folder', default='./muon-airss-out'):
    validate_str,
    # The symbol to use for the muon when writing out the castep custom
    # species.
    Optional('mu_symbol', default='H:mu'):
    validate_str,
    # Maximum number of geometry optimisation steps
    Optional('geom_steps', default=30):
    int,
    # Tolerance on geometry optimisation in units of eV/AA.
    Optional('geom_force_tol', default=0.5):
    float,
    # Max number of SCC steps to perform before giving up. Default is
    # 200 which is also the default for DFTB+.
    Optional('max_scc_steps', default=200):
    int,
})

# Parameter file schema and defaults
MuonHarmonicSchema = Schema({
    # File containing structural info about molecule/crystal
    'cell_file': validate_str,
    # Method used to calculate thermal average
    Optional('method', default='independent'): validate_all_of('independent', 'thermal'),
    # Index of muon in cell
    Optional('mu_index', default=-1): int,
    # If using Castep custom species, custom species of muon (supersedes index
    # if present in cell)
    Optional('mu_symbol', default='H:mu'): validate_str,
    # Number of grid points to use on each phonon mode or pairs of thermal lines
    Optional('grid_n', default=20): int,
    # Number of sigmas to sample in the harmonic approximation
    Optional('sigma_n', default=3): float,
    # Property to be calculated, currently accepted values: 'hyperfine' (hyperfine
    # coupling tensors), 'bandstructure'
    Optional('avgprop', default='hyperfine'): validate_all_of('hyperfine', 'bandstructure'),
    # Source of phonon modes, currently accepted values: "castep", "asedftb+"
    Optional('phonon_source', default='castep'): validate_all_of('castep', 'asedftbp'),
    # If using DFTB+ and ASE, which parametrization to use
    Optional('asedftbp_pars', default='3ob-3-1'): validate_all_of('3ob-3-1', 'pbc-0-3'),
    # If using DFTB+ and ASE, which kpoint grid to use
    Optional('asedftbp_kpts', default=[1, 1, 1]): validate_int_array,
    # Output parameters
    # Path of parameter file which can be copied into folders with displaced cell
    # files for convenience
    Optional('castep_out_param', default=None): validate_str,
    # k-point grid for output files, default is [1,1,1]
    Optional('castep_out_kpts', default=[1, 1, 1]): validate_int3,
})

# Parameter file schema and defaults
UEPSchema = Schema({
    # Starting position for muon (absolute coordinates)
    Optional('mu_pos', default=[0.0, 0.0, 0.0]):
    validate_float3,
    # Path from which to load the charge density
    Optional('chden_path', default=''):
    validate_str,
    # Seedname for the charge density calculation
    Optional('chden_seed', default=None):
    validate_str,
    # Maximum number of geometry optimisation steps
    Optional('geom_steps', default=30):
    int,
    # Tolerance on optimisation
    Optional('opt_tol', default=1e-5):
    float,
    # Gaussian Width factor for ionic potential
    Optional('gw_factor', default=5.0):
    float,
    # Optimisation method
    Optional('opt_method', default='trust-exact'):
    validate_str,
    # Particle mass (in kg)
    Optional('particle_mass',
             default=pcnst['muon mass'][0]):
    float,
    # Sorting for output file
    Optional('sorted_by', default='tot_energy'):
    validate_all_of('tot_energy', 'pot_energy', 'kin_energy')
})
