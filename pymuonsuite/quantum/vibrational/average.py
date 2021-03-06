"""
average.py

Quantum vibrational averages
"""

# Python 2-to-3 compatibility code
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import glob
import pickle
import numpy as np
from copy import deepcopy

from ase import io, Atoms
from ase.io.castep import read_param
from ase.calculators.castep import Castep
from ase.calculators.dftb import Dftb
from soprano.utils import seedname
from soprano.collection import AtomsCollection

# Internal imports
from pymuonsuite import constants
from pymuonsuite.io.castep import (parse_castep_masses, castep_write_input,
                                   add_to_castep_block)
from pymuonsuite.io.dftb import (dftb_write_input, load_muonconf_dftb,
                                 parse_spinpol_dftb)
from pymuonsuite.io.magres import parse_hyperfine_magres
from pymuonsuite.quantum.vibrational.phonons import ase_phonon_calc
from pymuonsuite.quantum.vibrational.schemes import (IndependentDisplacements,
                                                     MonteCarloDisplacements)
from pymuonsuite.calculate.hfine import compute_hfine_mullpop


class MuonAverageError(Exception):
    pass


def read_castep_gamma_phonons(seed, path='.'):
    """Parse CASTEP phonon data into a casteppy object,
    and return eigenvalues and eigenvectors at the gamma point.
    """

    try:
        from euphonic.data.phonon import PhononData
    except ImportError:
        raise ImportError("""
    Can't use castep phonon interface due to Euphonic not being installed.
    Please download and install Euphonic from Github:

    HTTPS:  https://github.com/pace-neutrons/Euphonic.git
    SSH:    git@github.com:pace-neutrons/Euphonic.git

    and try again.""")

    # Parse CASTEP phonon data into casteppy object
    try:
        pd = PhononData(seed, path=path)
    except TypeError:
        # This happens in newer versions of Euphonic
        pd = PhononData.from_castep(seed, path=path)        
    # Convert frequencies back to cm-1
    pd.convert_e_units('1/cm')
    # Get phonon frequencies+modes
    evals = np.array(pd.freqs.magnitude)
    evecs = np.array(pd.eigenvecs)

    # Only grab the gamma point!
    gamma_i = None
    for i, q in enumerate(pd.qpts):
        if np.isclose(q, [0, 0, 0]).all():
            gamma_i = i
            break

    if gamma_i is None:
        raise MuonAverageError('Could not find gamma point phonons in CASTEP'
                               ' phonon file')

    return evals[gamma_i], evecs[gamma_i]


def create_hfine_castep_calculator(mu_symbol='H:mu', calc=None, param_file=None,
                                   kpts=[1, 1, 1]):
    """Create a calculator containing all the necessary parameters
    for a hyperfine calculation."""

    if not isinstance(calc, Castep):
        calc = Castep()
    else:
        calc = deepcopy(calc)

    gamma_block = calc.cell.species_gamma.value
    calc.cell.species_gamma = add_to_castep_block(gamma_block, mu_symbol,
                                                  constants.m_gamma, 'gamma')

    calc.cell.kpoint_mp_grid = kpts

    if param_file is not None:
        calc.param = read_param(param_file).param

    calc.param.task = 'Magres'
    calc.param.magres_task = 'Hyperfine'

    return calc


def create_spinpol_dftbp_calculator(calc=None, param_set='3ob-3-1',
                                    kpts=None):
    """Create a calculator containing all necessary parameters for a DFTB+
    SCC spin polarised calculation"""
    from pymuonsuite.data.dftb_pars import DFTBArgs

    if not isinstance(calc, Dftb):
        calc = Dftb()
    else:
        calc = deepcopy(calc)

    # A bit of a hack for the k-points
    if kpts is not None:
        kc = Dftb(kpts=kpts)
        kargs = {k: v for k, v in kc.parameters.items() if 'KPoints' in k}
        calc.parameters.update(kargs)

    # Create the arguments
    dargs = DFTBArgs(param_set)
    # Make it spin polarised
    try:
        dargs.set_optional('spinpol.json', True)
    except KeyError:
        raise ValueError('DFTB+ parameter set does not allow spin polarised'
                         ' calculations')
    # Fix a few things, and add a spin on the muon
    args = dargs.args
    del(args['Hamiltonian_SpinPolarisation'])
    args['Hamiltonian_SpinPolarisation_'] = 'Colinear'
    args['Hamiltonian_SpinPolarisation_UnpairedElectrons'] = 1
    args['Hamiltonian_SpinPolarisation_InitialSpins_'] = ''
    args['Hamiltonian_SpinPolarisation_InitialSpins_Atoms'] = '-1'
    args['Hamiltonian_SpinPolarisation_InitialSpins_SpinPerAtom'] = 1

    calc.parameters.update(args)
    calc.do_forces = True

    return calc


def read_output_castep(folder, avgprop='hyperfine'):

    # Read a castep file in the given folder, and then the required property
    cfile = glob.glob(os.path.join(folder, '*.castep'))[0]
    sname = seedname(cfile)
    a = io.read(cfile)
    a.info['name'] = sname

    # Now read properties depending on what's being measured
    if avgprop == 'hyperfine':
        m = parse_hyperfine_magres(os.path.join(folder, sname + '.magres'))
        a.arrays.update(m.arrays)

    return a


def read_output_dftbp(folder, avgprop='hyperfine'):

    # Read a DFTB+ file in the given folder, and then the required property
    a = load_muonconf_dftb(folder)
    a.info['name'] = os.path.split(folder)[-1]

    if avgprop == 'hyperfine':
        pops = parse_spinpol_dftb(folder)
        hfine = []
        for i in range(len(a)):
            hf = compute_hfine_mullpop(a, pops, self_i=i, fermi=True,
                                       fermi_neigh=True)
            hfine.append(hf)
        a.set_array('hyperfine', np.array(hfine))

    return a


def muon_vibrational_average_write(cell_file, method='independent', mu_index=-1,
                                   mu_symbol='H:mu', grid_n=20, sigma_n=3,
                                   avgprop='hyperfine', calculator='castep',
                                   displace_T=0,
                                   phonon_source_file=None,
                                   phonon_source_type='castep',
                                   **kwargs):
    """
    Write input files to compute a vibrational average for a quantity on a muon
    in a given system.

    | Pars:
    |   cell_file (str):    Filename for input structure file
    |   method (str):       Method to use for the average. Options are 'independent',
    |                       'montecarlo'. Default is 'independent'.
    |   mu_index (int):     Position of the muon in the given cell file.
    |                       Default is -1.
    |   mu_symbol (str):    Use this symbol to look for the muon among
    |                       CASTEP custom species. Overrides muon_index if
    |                       present in cell.
    |   grid_n (int):       Number of configurations used for sampling. Applies slightly
    |                       differently to different schemes.
    |   sigma_n (int):      Number of sigmas of the harmonic wavefunction used
    |                       for sampling.
    |   avgprop (str):      Property to calculate and average. Default is 'hyperfine'.
    |   calculator (str):   Source of the property to calculate and average.
    |                       Can be 'castep' or 'dftb+'. Default is 'castep'.
    |   phonon_source (str):Source of the phonon data. Can be 'castep' or 'asedftbp'.
    |                       Default is 'castep'.
    |   **kwargs:           Other arguments (such as specific arguments for the given
    |                       phonon method)
    """

    # Open the structure file
    cell = io.read(cell_file)
    path = os.path.split(cell_file)[0]
    sname = seedname(cell_file)
    num_atoms = len(cell)

    cell.info['name'] = sname

    # Fetch species
    try:
        species = cell.get_array('castep_custom_species')
    except KeyError:
        species = np.array(cell.get_chemical_symbols())

    mu_indices = np.where(species == mu_symbol)[0]
    if len(mu_indices) > 1:
        raise MuonAverageError(
            'More than one muon found in the system')
    elif len(mu_indices) == 1:
        mu_index = mu_indices[0]
    else:
        species = list(species)
        species[mu_index] = mu_symbol
        species = np.array(species)

    cell.set_array('castep_custom_species', species)

    # Load the phonons
    if phonon_source_type == 'castep':
        if phonon_source_file is not None:
            phpath, phfile = os.path.split(phonon_source_file)
            phfile = seedname(phfile)
        else:
            phpath = path
            phfile = sname
        ph_evals, ph_evecs = read_castep_gamma_phonons(phfile, phpath)
    elif phonon_source_type == 'dftb+':
        if phonon_source_file is None:
            phonon_source_file = os.path.join(path, sname + '.phonons.pkl')

        with open(phonon_source_file, 'rb') as f:
            phdata = pickle.load(f)
            # Find the gamma point
            gamma_i = None
            for i, p in enumerate(phdata.path):
                if (p == 0).all():
                    gamma_i = i
                    break
            try:
                ph_evals = phdata.frequencies[gamma_i]
                ph_evecs = phdata.modes[gamma_i]
            except TypeError:
                raise RuntimeError(('Phonon file {0} does not contain gamma '
                                    'point data').format(phonon_source_file))

    # Fetch masses
    try:
        masses = parse_castep_masses(cell)
    except AttributeError:
        # Just fall back on ASE standard masses if not available
        masses = cell.get_masses()
    masses[mu_index] = constants.m_mu_amu
    cell.set_masses(masses)

    # Now create the distribution scheme
    if method == 'independent':
        displsch = IndependentDisplacements(ph_evals, ph_evecs, masses,
                                            mu_index, sigma_n)
    elif method == 'montecarlo':
        displsch = MonteCarloDisplacements(ph_evals, ph_evecs, masses)

    displsch.recalc_displacements(n=grid_n, T=displace_T)

    # Make it a collection
    pos = cell.get_positions()
    displaced_cells = []
    for i, d in enumerate(displsch.displacements):
        dcell = cell.copy()
        dcell.set_positions(pos + d)
        if calculator == 'dftb' and not kwargs['dftb_pbc']:
            dcell.set_pbc(False)
        dcell.info['name'] = sname + '_displaced_{0}'.format(i)
        displaced_cells.append(dcell)

    if kwargs['write_allconf']:
        # Write a global configuration structure
        allconf = sum(displaced_cells, cell.copy())
        if all(allconf.get_pbc()):
            io.write(sname + '_allconf.cell', allconf)
        else:
            io.write(sname + '_allconf.xyz', allconf)

    # Get a calculator
    if calculator == 'castep':
        writer_function = castep_write_input
        calc = create_hfine_castep_calculator(mu_symbol=mu_symbol,
                                              calc=cell.calc,
                                              param_file=kwargs['castep_param'],
                                              kpts=kwargs['k_points_grid'])
    elif calculator == 'dftb+':
        writer_function = dftb_write_input
        kpts = kwargs['k_points_grid'] if kwargs['dftb_pbc'] else None
        calc = create_spinpol_dftbp_calculator(
            param_set=kwargs['dftb_set'], kpts=kpts)

    displaced_coll = AtomsCollection(displaced_cells)
    displaced_coll.info['displacement_scheme'] = displsch
    displaced_coll.info['muon_index'] = mu_index
    displaced_coll.save_tree(sname + '_displaced', writer_function,
                             opt_args={'calc': calc,
                                       'script': kwargs['script_file']})


def muon_vibrational_average_read(cell_file, calculator='castep',
                                  avgprop='hyperfine', average_T=0,
                                  average_file='averages.dat',
                                  **kwargs):
    # Open the structure file
    cell = io.read(cell_file)
    path = os.path.split(cell_file)[0]
    sname = seedname(cell_file)
    num_atoms = len(cell)

    reader_function = {
        'castep': read_output_castep,
        'dftb+': read_output_dftbp
    }[calculator]

    displaced_coll = AtomsCollection.load_tree(sname + '_displaced',
                                               reader_function,
                                               safety_check=2,
                                               opt_args={
                                                   'avgprop': avgprop
                                               })
    mu_i = displaced_coll.info['muon_index']
    displsch = displaced_coll.info['displacement_scheme']

    to_avg = []

    for a in displaced_coll:
        if avgprop == 'hyperfine':
            to_avg.append(a.get_array('hyperfine')[mu_i])
        elif avgprop == 'charge':
            # Used mostly as test
            to_avg.append(a.get_charges()[mu_i])

    to_avg = np.array(to_avg)
    displsch.recalc_weights(T=average_T)
    # New shape
    N = len(displaced_coll)
    shape = tuple([slice(N)] + [None]*(len(to_avg.shape)-1))
    avg = np.sum(displsch.weights[shape]*to_avg, axis=0)

    # Print output report
    with open(average_file, 'w') as f:
        avgname = {
            'hyperfine': 'hyperfine tensor',
            'charge': 'charge'
        }[avgprop]
        f.write("""
Quantum average of {property} calculated on {cell}.
Scheme details:

{scheme}

Averaged value:

{avg}

All values, by configuration:

{vals}

        """.format(property=avgname, cell=cell_file,
                   scheme=displsch, avg=avg, vals='\n'.join([
                       '{0}\n{1}\n'.format(i, v)
                       for i, v in enumerate(to_avg)])))
