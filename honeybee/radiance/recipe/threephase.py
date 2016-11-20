from .annual import HBAnnualAnalysisRecipe
from ..postprocess.annualresults import LoadAnnualsResults
from ..parameters.rfluxmtx import RfluxmtxParameters
from ..parameters.xform import XformParameters
from ..command.dctimestep import Dctimestep
from ..command.rfluxmtx import Rfluxmtx
from ..command.epw2wea import Epw2wea
from ..command.gendaymtx import Gendaymtx
from ..command.rmtxop import Rmtxop
from ..command.xform import Xform
from ..sky.skymatrix import SkyMatrix

from ...helper import preparedir, getRadiancePathLines

import os
import subprocess


class HBThreePhaseAnalysisRecipe(HBAnnualAnalysisRecipe):
    """Annual analysis recipe.

    Attributes:

        hbObjects: An optional list of Honeybee surfaces or zones (Default: None).
        subFolder: Analysis subfolder for this recipe. (Default: "sunlighthours")

    Usage:
        # initiate analysisRecipe
        analysisRecipe = HBAnnualAnalysisRecipe(
            epwFile, testPoints, ptsVectors
            )

        # add honeybee object
        analysisRecipe.hbObjects = HBObjs

        # write analysis files to local drive
        analysisRecipe.writeToFile(_folder_, _name_)

        # run the analysis
        analysisRecipe.run(debaug=False)

        # get the results
        print analysisRecipe.results()
    """

    def __init__(self, skyMtx, analysisGrids, reuseDaylightMtx=True, hbObjects=None,
                 subFolder="threephase"):
        """Create an annual recipe."""
        HBAnnualAnalysisRecipe.__init__(
            self, skyMtx, analysisGrids, hbObjects, subFolder
        )

        self.reuseDaylightMtx = reuseDaylightMtx

        # set RfluxmtxParameters as default radiance parameter for annual analysis
        self.__radianceParameters = RfluxmtxParameters()
        self.__radianceParameters.irradianceCalc = True

        # @sarith do we want to set these values as default?
        self.__radianceParameters.ambientAccuracy = 0.1
        self.__radianceParameters.ambientDivisions = 4096
        self.__radianceParameters.ambientBounces = 6
        self.__radianceParameters.limitWeight = 0.001

        self.__batchFile = None
        self.resultsFile = []

        # create a result loader to load the results once the analysis is done.
        self.loader = LoadAnnualsResults(self.resultsFile)

    @classmethod
    def fromWeatherFilePointsAndVectors(
        cls, epwFile, pointGroups, vectorGroups=None, skyDensity=1,
            reuseDaylightMtx=True, hbObjects=None, subFolder="threephase"):
        """Create three-phase recipe from weather file, points and vectors.

        Args:
            epwFile: An EnergyPlus weather file.
            pointGroups: A list of (x, y, z) test points or lists of (x, y, z)
                test points. Each list of test points will be converted to a
                TestPointGroup. If testPts is a single flattened list only one
                TestPointGroup will be created.
            vectorGroups: An optional list of (x, y, z) vectors. Each vector
                represents direction of corresponding point in testPts. If the
                vector is not provided (0, 0, 1) will be assigned.
            skyDensity: A positive intger for sky density. 1: Tregenza Sky,
                2: Reinhart Sky, etc. (Default: 1)
            hbObjects: An optional list of Honeybee surfaces or zones (Default: None).
            subFolder: Analysis subfolder for this recipe. (Default: "sunlighthours")
        """
        assert epwFile.lower().endswith('.epw'), \
            ValueError('{} is not a an EnergyPlus weather file.'.format(epwFile))
        skyMtx = SkyMatrix(epwFile, skyDensity)
        analysisGrids = cls.analysisGridsFromPointsAndVectors(pointGroups,
                                                              vectorGroups)

        return cls(skyMtx, analysisGrids, hbObjects, reuseDaylightMtx, subFolder)

    @classmethod
    def fromPointsFile(cls, epwFile, pointsFile, skyDensity=1,
                       reuseDaylightMtx=True, hbObjects=None,
                       subFolder="threephase"):
        """Create an annual recipe from points file."""
        try:
            with open(pointsFile, "rb") as inf:
                pointGroups = tuple(line.split()[:3] for line in inf.readline())
                vectorGroups = tuple(line.split()[3:] for line in inf.readline())
        except:
            raise ValueError("Couldn't import points from {}".format(pointsFile))

        return cls.fromWeatherFilePointsAndVectors(
            epwFile, pointGroups, vectorGroups, skyDensity, hbObjects,
            reuseDaylightMtx, subFolder)

    @property
    def radianceParameters(self):
        """Radiance parameters for annual analysis."""
        return self.__radianceParameters

    @property
    def skyType(self):
        """Radiance sky type e.g. r1, r2, r4."""
        return "r{}".format(self.skyMatrix.skyDensity)

    # TODO: Add path to PATH and use relative path in batch files
    # TODO: @sariths docstring should be modified
    def writeToFile(self, targetFolder, projectName, radFiles=None,
                    useRelativePath=False):
        """Write analysis files to target folder.

        Files for sunlight hours analysis are:
            test points <projectName.pts>: List of analysis points.
            material file <*.mat>: Radiance materials. Will be empty if HBObjects is None.
            geometry file <*.rad>: Radiance geometries. Will be empty if HBObjects is None.
            batch file <*.bat>: An executable batch file which has the list of commands.
                oconv [material file] [geometry file] [sun materials file] [sun geometries file] > [octree file]
                rcontrib -ab 0 -ad 10000 -I -M [sunlist.txt] -dc 1 [octree file]< [pts file] > [rcontrib results file]

        Args:
            targetFolder: Path to parent folder. Files will be created under
                targetFolder/gridbased. use self.subFolder to change subfolder name.
            projectName: Name of this project as a string.
            radFiles: A list of additional .rad files to be added to the scene
            useRelativePath: Set to True to use relative path in bat file <NotImplemented!>.

        Returns:
            True in case of success.
        """
        # 0.prepare target folder
        # create main folder targetFolder\projectName
        _basePath = os.path.join(targetFolder, projectName)
        _ispath = preparedir(_basePath)
        assert _ispath, "Failed to create %s. Try a different path!" % _basePath

        # create main folder targetFolder\projectName\threephase
        _path = os.path.join(_basePath, self.subFolder)
        _ispath = preparedir(_path)

        assert _ispath, "Failed to create %s. Try a different path!" % _path

        # Check if anything has changed
        # if not self.isChanged:
        #     print "Inputs has not changed! Check files at %s" % _path

        # 0.create a place holder for batch file
        batchFileLines = []
        # add path if needed
        batchFileLines.append(getRadiancePathLines())

        # TODO: This line won't work in linux.
        dirLine = "%s\ncd %s" % (os.path.splitdrive(_path)[0], _path)
        batchFileLines.append(dirLine)

        # 1.write points
        pointsFile = self.writePointsToFile(_path, projectName)

        # 2.write materials and geometry files
        matFile, geoFile = self.writeHBObjectsToFile(_path, projectName)

        # 3.0. find glazing items with .xml material, write them to a separate
        # file and invert them
        bsdfGlazing = tuple(f for f in self.hbObjects
                            if hasattr(f.radianceMaterial, 'xmlfile'))[0]

        tMatrix = bsdfGlazing.radianceMaterial.xmlfile

        glssPath = os.path.join(_path, 'glazing.rad')
        glssRevPath = os.path.join(_path, 'glazingI.rad')
        bsdfGlazing.radStringToFile(glssPath)

        xfrParam = XformParameters()
        xfrParam.invertSurfaces = True

        xfr = Xform()
        xfr.xformParameters = xfrParam
        xfr.radFile = glssPath
        xfr.outputFile = glssRevPath
        batchFileLines.append(xfr.toRadString())

        # # 3.1.Create annual daylight vectors through epw2wea and gendaymtx.
        skyMtx = self.skyMatrix.execute(_path)

        # # 3.2.Generate view matrix
        rflux = Rfluxmtx(projectName)
        rflux.sender = '-'
        rflux.rfluxmtxParameters = None
        rflux.rfluxmtxParameters.irradianceCalc = True
        rflux.rfluxmtxParameters.ambientAccuracy = 0.1
        rflux.rfluxmtxParameters.ambientBounces = 10
        rflux.rfluxmtxParameters.ambientDivisions = 65536
        rflux.rfluxmtxParameters.limitWeight = 1E-5

        # This needs to be automated based on the normal of each window.
        # Klems full basis sampling and the window faces +Y
        recCtrlPar = rflux.ControlParameters(hemiType='kf', hemiUpDirection='+Z')
        rflux.receiverFile = rflux.addControlParameters(
            glssPath, {bsdfGlazing.radianceMaterial.name: recCtrlPar})

        rflux.radFiles = (matFile, geoFile, 'glazing.rad')
        rflux.pointsFile = pointsFile
        rflux.outputMatrix = projectName + ".vmx"
        batchFileLines.append(rflux.toRadString())
        vMatrix = rflux.outputMatrix

        # 3.3 daylight matrix
        rflux2 = Rfluxmtx()
        rflux2.samplingRaysCount = 1000
        rflux2.sender = 'glazingI.rad_m'
        skyFile = rflux2.defaultSkyGround(
            os.path.join(_path, 'rfluxSky.rad'),
            skyType='r{}'.format(self.skyMatrix.skyDensity))

        rflux2.receiverFile = skyFile
        rflux2.rfluxmtxParameters = None
        rflux2.rfluxmtxParameters.ambientAccuracy = 0.1
        rflux2.rfluxmtxParameters.ambientDivisions = 1024
        rflux2.rfluxmtxParameters.ambientBounces = 2
        rflux2.rfluxmtxParameters.limitWeight = 0.0000001
        rflux2.radFiles = (matFile, geoFile, 'glazing.rad')
        rflux2.outputMatrix = projectName + ".dmx"
        batchFileLines.append(rflux2.toRadString())
        dMatrix = rflux2.outputMatrix

        # 4. matrix calculations
        dct = Dctimestep()
        dct.tmatrixFile = tMatrix
        dct.vmatrixSpec = vMatrix
        dct.dmatrixFile = str(dMatrix)
        dct.skyVectorFile = skyMtx
        dct.outputFileName = r"illuminance.ill"
        batchFileLines.append(dct.toRadString())

        # 5. write batch file
        batchFile = os.path.join(_path, projectName + ".bat")
        self.write(batchFile, "\n".join(batchFileLines))
        self.__batchFile = batchFile

        print "Files are written to: %s" % _path
        return _path

    # TODO: Update the method to batch run and move it to baseclass
    def run(self, debug=False):
        """Run the analysis."""
        if self.__batchFile:
            if debug:
                with open(self.__batchFile, "a") as bf:
                    bf.write("\npause")

            subprocess.call(self.__batchFile)

            self.isCalculated = True
            # self.isChanged = False

            self.resultsFile = [os.path.join(os.path.split(self.__batchFile)[0],
                                             "illuminance.ill")]
            return True
        else:
            raise Exception("You need to write the files before running the recipe.")

    def results(self, flattenResults=True):
        """Return results for this analysis."""
        assert self.isCalculated, \
            "You haven't run the Recipe yet. Use self.run " + \
            "to run the analysis before loading the results."

        self.loader.resultFiles = self.resultsFile
        return self.loader.results
