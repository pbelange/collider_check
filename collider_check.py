#### Imports
import numpy as np
import json
import xtrack as xt
import yaml
from scipy import constants
from functools import lru_cache
import matplotlib.pyplot as plt


class ColliderCheck:
    def __init__(self, collider):
        """Initialize the ColliderCheck class directly from a collider, potentially embedding a
        configuration file."""

        # Store the collider
        self.collider = collider

        # Define the configuration through a property since it might not be there
        self._configuration = None
        self.configuration_str = None

        # Beam energy
        self.energy = self.collider.lhcb1.particle_ref._p0c[0] / 1e9

        # Get twiss and survey dataframes for both beams
        self.tw_b1, self.sv_b1 = [self.collider.lhcb1.twiss(), self.collider.lhcb1.survey()]
        self.tw_b2, self.sv_b2 = [self.collider.lhcb2.twiss(), self.collider.lhcb2.survey()]
        self.df_tw_b1, self.df_sv_b1 = [self.tw_b1.to_pandas(), self.sv_b1.to_pandas()]
        self.df_tw_b2, self.df_sv_b2 = [self.tw_b2.to_pandas(), self.sv_b2.to_pandas()]

        # Variables used to compute the separation (computed on the fly)
        self.dic_survey_per_ip = {"lhcb1": {}, "lhcb2": {}}

    @property
    def configuration(self):
        """Loads the configuration, as well as the luminosity and filling scheme arrays, if they're
        not already loaded."""

        if self._configuration is not None:
            return self._configuration
        else:
            # Get the corresponding configuration if it's there
            if hasattr(self.collider, "metadata"):
                self._configuration = self.collider.metadata
                self._update_attributes_configuration()

        return self._configuration

    @configuration.setter
    def configuration(self, configuration_dict):
        """This function is used to update the configuration, and the attributes that depend on it."""
        self._configuration = configuration_dict
        self._update_attributes_configuration()

    def _update_attributes_configuration(self):
        # Ensure that the configuration format is correct
        if "config_collider" not in self.configuration:
            print("Warning, the provided configuration doesn't embed mad configuration.")
            self.configuration = {
                "config_collider": self.configuration,
                "config_mad": {},
            }
            self.configuration_str = yaml.dump(self.configuration)

        # Compute luminosity and filling schemes attributes
        self._load_configuration_luminosity()
        self._load_filling_scheme_arrays()

        # Clean cache for separation computation
        self.compute_separation_variables.cache_clear()

    @property
    def nemitt_x(self):
        if self.configuration is not None:
            return self.configuration["config_collider"]["config_beambeam"]["nemitt_x"]
        else:
            print("Warning: no configuration provided. Using default value of 2.2e-6 for nemitt_x.")
            return 2.2e-6

    @property
    def nemitt_y(self):
        if self.configuration is not None:
            return self.configuration["config_collider"]["config_beambeam"]["nemitt_y"]
        else:
            print("Warning: no configuration provided. Using default value of 2.2e-6 for nemitt_y.")
            return 2.2e-6

    def _check_configuration(self):
        if self.configuration is None:
            raise ValueError(
                "No configuration has been provided when instantiating the ColliderCheck object."
            )

    def _load_configuration_luminosity(self):
        if (
            "num_particles_per_bunch_after_optimization"
            in self.configuration["config_collider"]["config_beambeam"]
        ):
            self.num_particles_per_bunch = float(
                self.configuration["config_collider"]["config_beambeam"][
                    "num_particles_per_bunch_after_optimization"
                ]
            )
        else:
            print(
                "Warning: no num_particles_per_bunch_after_optimization provided in the config"
                " file. Using the one from the configuration before optimization."
            )
            self.num_particles_per_bunch = float(
                self.configuration["config_collider"]["config_beambeam"]["num_particles_per_bunch"]
            )

        self.sigma_z = self.configuration["config_collider"]["config_beambeam"]["sigma_z"]

    def _load_filling_scheme_arrays(self):
        # Then get the filling scheme path (should already be an absolute path)
        self.path_filling_scheme = self.configuration["config_collider"]["config_beambeam"][
            "mask_with_filling_pattern"
        ]["pattern_fname"]

        # Load the scheme (two boolean arrays representing the buckets in the two beams)
        with open(self.path_filling_scheme) as fid:
            filling_scheme = json.load(fid)

        self.array_b1 = np.array(filling_scheme["beam1"])
        self.array_b2 = np.array(filling_scheme["beam2"])

        # Get the bunches selected for tracking
        self.i_bunch_b1 = self.configuration["config_collider"]["config_beambeam"][
            "mask_with_filling_pattern"
        ]["i_bunch_b1"]
        self.i_bunch_b2 = self.configuration["config_collider"]["config_beambeam"][
            "mask_with_filling_pattern"
        ]["i_bunch_b2"]

    def return_number_of_collisions(self, IP=1):
        """Computes and returns the number of collisions at the requested IP."""

        # Ensure configuration is defined
        self._check_configuration()

        # Assert that the arrays have the required length, and do the convolution
        assert len(self.array_b1) == len(self.array_b2) == 3564
        if IP == 1 or IP == 5:
            return self.array_b1 @ self.array_b2
        elif IP == 2:
            return np.roll(self.array_b1, -891) @ self.array_b2
        elif IP == 8:
            return np.roll(self.array_b1, -2670) @ self.array_b2
        else:
            raise ValueError("IP must be either 1, 2, 5 or 8.")

    def return_luminosity(self, IP=1, crab=False):
        """Computes and returns the luminosity at the requested IP. External twiss (e.g. from before
        beam-beam) can be provided."""

        # Ensure configuration is defined
        self._check_configuration()

        if IP not in [1, 2, 5, 8]:
            raise ValueError("IP must be either 1, 2, 5 or 8.")
        n_col = self.return_number_of_collisions(IP=IP)
        luminosity = xt.lumi.luminosity_from_twiss(
            n_colliding_bunches=n_col,
            num_particles_per_bunch=self.num_particles_per_bunch,
            ip_name="ip" + str(IP),
            nemitt_x=self.nemitt_x,
            nemitt_y=self.nemitt_y,
            sigma_z=self.sigma_z,
            twiss_b1=self.tw_b1,
            twiss_b2=self.tw_b2,
            crab=crab,
        )
        return luminosity

    def return_twiss_at_ip(self, beam=1, ip=1):
        """Returns the twiss parameters, position and angle at the requested IP."""
        if beam == 1:
            return (
                self.tw_b1.rows[f"ip{ip}"]
                .cols["s", "x", "px", "y", "py", "betx", "bety", "dx", "dy"]
                .to_pandas()
            )
        elif beam == 2:
            return (
                self.tw_b2.rows[f"ip{ip}"]
                .cols["s", "x", "px", "y", "py", "betx", "bety", "dx", "dy"]
                .to_pandas()
            )
        else:
            raise ValueError("Beam must be either 1 or 2.")

    def return_tune_and_chromaticity(self, beam=1):
        """Returns the tune and chromaticity for the requested beam."""
        if beam == 1:
            return self.tw_b1["qx"], self.tw_b1["dqx"], self.tw_b1["qy"], self.tw_b1["dqy"]
        elif beam == 2:
            return self.tw_b2["qx"], self.tw_b2["dqx"], self.tw_b2["qy"], self.tw_b2["dqy"]
        else:
            raise ValueError("Beam must be either 1 or 2.")

    def return_linear_coupling(self):
        """Returns the linear coupling for the two beams."""
        return self.tw_b1["c_minus"], self.tw_b2["c_minus"]

    def return_momentum_compaction_factor(self):
        """Returns the momentum compaction factor for the two beams."""
        return self.tw_b1["momentum_compaction_factor"], self.tw_b2["momentum_compaction_factor"]

    def return_polarity_ip_2_8(self):
        """Return the polarity (internal angle of the experiments) for IP2 and IP8."""
        # Ensure configuration is defined
        self._check_configuration()

        polarity_alice = self.configuration["config_collider"]["config_knobs_and_tuning"][
            "knob_settings"
        ]["on_alice_normalized"]
        polarity_lhcb = self.configuration["config_collider"]["config_knobs_and_tuning"][
            "knob_settings"
        ]["on_lhcb_normalized"]

        return polarity_alice, polarity_lhcb

    def _compute_ip_specific_separation(self, ip="ip1", beam_weak="b1"):
        # Compute survey at IP if needed
        if ip not in self.dic_survey_per_ip["lhcb1"] or ip not in self.dic_survey_per_ip["lhcb2"]:
            self.dic_survey_per_ip["lhcb1"][ip] = self.collider["lhcb1"].survey(element0=ip)
            self.dic_survey_per_ip["lhcb2"][ip] = (
                self.collider["lhcb2"].survey(element0=ip).reverse()
            )

        # Define strong and weak beams
        if beam_weak == "b1":
            beam_strong = "b2"
            twiss_weak = self.tw_b1
            twiss_strong = self.tw_b2.reverse()
            survey_weak = self.dic_survey_per_ip["lhcb1"]
            survey_strong = self.dic_survey_per_ip["lhcb2"]
        else:
            beam_strong = "b1"
            twiss_weak = self.tw_b2.reverse()
            twiss_strong = self.tw_b1
            survey_weak = self.dic_survey_per_ip["lhcb2"]
            survey_strong = self.dic_survey_per_ip["lhcb1"]

        # Filter the twiss and surveys at the requested IP
        survey_filtered = {}
        twiss_filtered = {}
        my_filter_string = f"bb_(ho|lr)\.(r|l|c){ip[2]}.*"
        survey_filtered[beam_strong] = survey_strong[ip][["X", "Y", "Z"], my_filter_string]
        survey_filtered[beam_weak] = survey_weak[ip][["X", "Y", "Z"], my_filter_string]
        twiss_filtered[beam_strong] = twiss_strong[:, my_filter_string]
        twiss_filtered[beam_weak] = twiss_weak[:, my_filter_string]

        s = survey_filtered[beam_strong]["Z"]
        d_x_weak_strong_in_meter = (
            twiss_filtered[beam_weak]["x"]
            - twiss_filtered[beam_strong]["x"]
            + survey_filtered[beam_weak]["X"]
            - survey_filtered[beam_strong]["X"]
        )
        d_y_weak_strong_in_meter = (
            twiss_filtered[beam_weak]["y"]
            - twiss_filtered[beam_strong]["y"]
            + survey_filtered[beam_weak]["Y"]
            - survey_filtered[beam_strong]["Y"]
        )

        return (
            s,
            my_filter_string,
            beam_strong,
            twiss_filtered,
            survey_filtered,
            d_x_weak_strong_in_meter,
            d_y_weak_strong_in_meter,
        )

    def _compute_emittances_separation(self):
        # gamma relativistic of a proton at 7 TeV
        gamma_rel = self.energy / (
            constants.physical_constants["proton mass energy equivalent in MeV"][0] / 1000
        )
        # beta relativistic of a proton at 7 TeV
        beta_rel = np.sqrt(1 - 1 / gamma_rel**2)

        emittance_strong_x = self.nemitt_x / gamma_rel / beta_rel
        emittance_strong_y = self.nemitt_y / gamma_rel / beta_rel

        emittance_weak_x = self.nemitt_x / gamma_rel / beta_rel
        emittance_weak_y = self.nemitt_y / gamma_rel / beta_rel

        return (
            gamma_rel,
            beta_rel,
            emittance_weak_x,
            emittance_weak_y,
            emittance_strong_x,
            emittance_strong_y,
        )

    def _compute_ip_specific_normalized_separation(
        self,
        twiss_filtered,
        beam_weak,
        beam_strong,
        emittance_strong_x,
        emittance_strong_y,
        emittance_weak_x,
        emittance_weak_y,
        d_x_weak_strong_in_meter,
        d_y_weak_strong_in_meter,
    ):
        # Size of the strong beams
        sigma_x_strong = np.sqrt(twiss_filtered[beam_strong]["betx"] * emittance_strong_x)
        sigma_y_strong = np.sqrt(twiss_filtered[beam_strong]["bety"] * emittance_strong_y)

        # Size of the weak beams
        sigma_x_weak = np.sqrt(twiss_filtered[beam_weak]["betx"] * emittance_weak_x)
        sigma_y_weak = np.sqrt(twiss_filtered[beam_weak]["bety"] * emittance_weak_y)

        # Normalized separation
        dx_sig = d_x_weak_strong_in_meter / sigma_x_strong
        dy_sig = d_y_weak_strong_in_meter / sigma_y_strong

        # Flatness of the beam
        A_w_s = sigma_x_weak / sigma_y_strong
        B_w_s = sigma_y_weak / sigma_x_strong

        fw = 1
        r = sigma_y_strong / sigma_x_strong

        return (
            d_x_weak_strong_in_meter,
            d_y_weak_strong_in_meter,
            dx_sig,
            dy_sig,
            A_w_s,
            B_w_s,
            fw,
            r,
        )

    # Cache function to gain time
    @lru_cache(maxsize=20)
    def compute_separation_variables(self, ip="ip1", beam_weak="b1"):
        """This function computes all the variables needed to compute the separation at the
        requested IP, in a weak-strong setting. The variables are stored and return in a dictionnary.
        """

        # Get variables specific to the requested IP
        (
            s,
            my_filter_string,
            beam_strong,
            twiss_filtered,
            survey_filtered,
            d_x_weak_strong_in_meter,
            d_y_weak_strong_in_meter,
        ) = self._compute_ip_specific_separation(ip=ip, beam_weak=beam_weak)

        # Get emittances
        (
            gamma_rel,
            beta_rel,
            emittance_weak_x,
            emittance_weak_y,
            emittance_strong_x,
            emittance_strong_y,
        ) = self._compute_emittances_separation()

        # Get normalized separation
        d_x_weak_strong_in_meter, d_y_weak_strong_in_meter, dx_sig, dy_sig, A_w_s, B_w_s, fw, r = (
            self._compute_ip_specific_normalized_separation(
                twiss_filtered,
                beam_weak,
                beam_strong,
                emittance_strong_x,
                emittance_strong_y,
                emittance_weak_x,
                emittance_weak_y,
                d_x_weak_strong_in_meter,
                d_y_weak_strong_in_meter,
            )
        )

        # Stora all variables used dor separation computation in a dictionnary
        dic_separation = {
            "twiss_filtered": twiss_filtered,
            "survey_filtered": survey_filtered,
            "s": s,
            "dx_meter": d_x_weak_strong_in_meter,
            "dy_meter": d_y_weak_strong_in_meter,
            "dx_sig": dx_sig,
            "dy_sig": dy_sig,
            "A_w_s": A_w_s,
            "B_w_s": B_w_s,
            "fw": fw,
            "r": r,
            "emittance_strong_x": emittance_strong_x,
            "emittance_strong_y": emittance_strong_y,
            "emittance_weak_x": emittance_weak_x,
            "emittance_weak_y": emittance_weak_y,
            "gamma_rel": gamma_rel,
            "beta_rel": beta_rel,
            "energy": self.energy,
            "my_filter_string": my_filter_string,
            "beam_weak": beam_weak,
            "beam_strong": beam_strong,
            "ip": ip,
        }

        return dic_separation

    def plot_orbits(self, ip="ip1", beam_weak="b1"):
        """Plots the beams orbits at the requested IP."""

        # Get separation variables
        ip_dict = self.compute_separation_variables(ip=ip, beam_weak=beam_weak)

        # Do the plot
        plt.figure()
        plt.title(f'IP{ip_dict["ip"][2]}')
        beam_weak = ip_dict["beam_weak"]
        beam_strong = ip_dict["beam_strong"]
        twiss_filtered = ip_dict["twiss_filtered"]
        plt.plot(ip_dict["s"], twiss_filtered[beam_weak]["x"], "ob", label=f"x {beam_weak}")
        plt.plot(ip_dict["s"], twiss_filtered[beam_strong]["x"], "sb", label=f"x {beam_strong}")
        plt.plot(ip_dict["s"], twiss_filtered[beam_weak]["y"], "or", label=f"y {beam_weak}")
        plt.plot(ip_dict["s"], twiss_filtered[beam_strong]["y"], "sr", label=f"y {beam_strong}")
        plt.xlabel("s [m]")
        plt.ylabel("x,y [m]")
        plt.legend()
        plt.grid(True)
        plt.show()

    def plot_separation(self, ip="ip1", beam_weak="b1"):
        """Plots the normalized separation at the requested IP."""
        # Get separation variables
        ip_dict = self.compute_separation_variables(ip=ip, beam_weak=beam_weak)

        # Do the plot
        plt.figure()
        plt.title(f'IP{ip_dict["ip"][2]}')
        plt.plot(ip_dict["s"], np.abs(ip_dict["dx_sig"]), "ob", label="x")
        plt.plot(ip_dict["s"], np.abs(ip_dict["dy_sig"]), "sr", label="y")
        plt.xlabel("s [m]")
        plt.ylabel("separation in x,y [$\sigma$]")
        plt.legend()
        plt.grid(True)
        plt.show()

    def output_check_as_str(self, path_output=None):
        """Summarizes the collider observables in a string, and optionally to a file."""
        str_file = ""

        # Check tune and chromaticity
        qx_b1, dqx_b1, qy_b1, dqy_b1 = self.return_tune_and_chromaticity(beam=1)
        qx_b2, dqx_b2, qy_b2, dqy_b2 = self.return_tune_and_chromaticity(beam=2)
        str_file += "Tune and chromaticity\n"
        str_file += (
            f"Qx_b1 = {qx_b1:.4f}, Qy_b1 = {qy_b1:.4f}, dQx_b1 = {dqx_b1:.4f}, dQy_b1 ="
            f" {dqy_b1:.4f}\n"
        )
        str_file += (
            f"Qx_b2 = {qx_b2:.4f}, Qy_b2 = {qy_b2:.4f}, dQx_b2 = {dqx_b2:.4f}, dQy_b2 ="
            f" {dqy_b2:.4f}\n"
        )
        str_file += "\n\n"

        # Check linear coupling
        c_minus_b1, c_minus_b2 = self.return_linear_coupling()
        str_file += "Linear coupling\n"
        str_file += f"C- b1 = {c_minus_b1:.4f}, C- b2 = {c_minus_b2:.4f}\n"

        # Check momentum compaction factor
        alpha_p_b1, alpha_p_b2 = self.return_momentum_compaction_factor()
        str_file += "Momentum compaction factor\n"
        str_file += f"alpha_p b1 = {alpha_p_b1:.4f}, alpha_p b2 = {alpha_p_b2:.4f}\n"

        str_file += "\n\n"

        # Check twiss observables at all IPs
        str_file += "Twiss observables\n"
        for ip in [1, 2, 5, 8]:
            tw_b1 = self.return_twiss_at_ip(beam=1, ip=ip).to_string(index=False)
            tw_b2 = self.return_twiss_at_ip(beam=2, ip=ip).to_string(index=False)
            str_file += f"IP{ip} (beam 1)\n"
            str_file += tw_b1 + "\n"
            str_file += f"IP{ip} (beam 2)\n"
            str_file += tw_b2 + "\n"
            str_file += "\n"

        str_file += "\n\n"

        if self.configuration is not None:
            # Check luminosity
            lumi1 = self.return_luminosity(IP=1)
            lumi2 = self.return_luminosity(IP=2)
            lumi5 = self.return_luminosity(IP=5)
            lumi8 = self.return_luminosity(IP=8)
            str_file += "Luminosity\n"
            str_file += (
                f"IP1 = {lumi1:.4e}, IP2 = {lumi2:.4e}, IP5 = {lumi5:.4e}, IP8 = {lumi8:.4e}\n"
            )

            str_file += "\n\n"

        if path_output is not None:
            # Write to file
            with open(path_output, "w") as fid:
                fid.write(str_file)

        return str_file


if __name__ == "__main__":
    path_collider = "/afs/cern.ch/work/c/cdroin/private/example_DA_study/master_study/scans/all_optics_2023/collider_03/xtrack_0000/collider.json"
    with open(path_collider, "r") as fid:
        collider_dict = json.load(fid)
    if "config_yaml" in collider_dict:
        print("A configuration has been found in the collider file. Using it.")
        config = collider_dict["config_yaml"]
    else:
        print(
            "Warning, you provided a collider file without a configuration. Some features of"
            " the dashboard will be missing."
        )

    collider = xt.Multiline.from_dict(collider_dict)
    collider.metadata = config
    collider.build_trackers()

    # Do collider check
    collider_check = ColliderCheck(collider=collider)
    print(collider_check.output_check_as_str(path_output="check.txt"))
