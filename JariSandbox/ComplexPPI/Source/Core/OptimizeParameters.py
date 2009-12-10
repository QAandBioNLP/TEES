import sys, os, types
import combine
import time
sys.path.append(os.path.dirname(os.path.abspath(__file__))+"/..")
from Utils.Parameters import *
from Utils.Timer import Timer
import Utils.Stream as Stream
from Murska.CSCConnection import CSCConnection
import ExampleUtils

def getCombinationString(combination):
    string = ""
    for key in sorted(combination.keys()):
        if string != "":
            string += "-"
        string += str(key) + "_" + str(combination[key])
    return string

def getParameterCombinations(parameters):
    parameterNames = parameters.keys()
    parameterNames.sort()
    parameterValues = []
    for parameterName in parameterNames:
        parameterValues.append([])
        for value in parameters[parameterName]:
            parameterValues[-1].append( (parameterName,value) )
    combinationLists = combine.combine(*parameterValues)
    combinations = []
    for combinationList in combinationLists:
        combinations.append({})
        for value in combinationList:
            combinations[-1][value[0]] = value[1]
    return combinations

def optimize(Classifier, Evaluator, trainExamples, testExamples, classIds, parameters, workDir=None, timeout=None, cscConnection=None, downloadAllModels=False):
    print >> sys.stderr, "Optimizing parameters"
    if workDir != None:
        if not os.path.exists(workDir):
            print >> sys.stderr, "Creating optimization output directory", workDir
            os.mkdir(workDir)
            
    if type(parameters) == types.StringType:
        parameters = splitParameters(parameters)
    combinations = getParameterCombinations(parameters)
    
    if cscConnection == None:
        return optimizeLocal(Classifier, Evaluator, trainExamples, testExamples, classIds, combinations, workDir, timeout)
    else:
        return optimizeCSC(Classifier, Evaluator, trainExamples, testExamples, classIds, combinations, workDir, timeout, cscConnection, downloadAllModels)

def optimizeLocal(Classifier, Evaluator, trainExamples, testExamples, classIds, combinations, workDir=None, timeout=None):
    bestResult = None
    combinationCount = 1
    for combination in combinations:
        Stream.setIndent(" ")
        print >> sys.stderr, "Parameters "+str(combinationCount)+"/"+str(len(combinations))+":", str(combination)
        Stream.setIndent("  ")
        combinationId = getCombinationString(combination)
        # Train
        trainOutput = "model-" + combinationId
        if workDir != None:
            trainOutput = os.path.join(workDir, trainOutput)
        print >> sys.stderr, "Training..."
        timer = Timer()
        Classifier.train(trainExamples, combination, trainOutput)
        print >> sys.stderr, "Training Complete, time:", timer.toString()
        # Test
        testOutput = "classifications-" + combinationId
        if workDir != None:
            testOutput = os.path.join(workDir, testOutput)
        print >> sys.stderr, "Testing..."
        timer = Timer()
        Classifier.test(testExamples, trainOutput, testOutput)
        print >> sys.stderr, "Testing Complete, time:", timer.toString()
        # Evaluate
        evaluationOutput = "evaluation-" + combinationId + ".csv"
        if workDir != None:
            evaluationOutput = os.path.join(workDir, evaluationOutput)
        Stream.setIndent("   ")
        evaluator = Evaluator.evaluate(testExamples, testOutput, classIds, evaluationOutput)
        #print >> sys.stderr, evaluator.toStringConcise("  ")

        if bestResult == None or evaluator.compare(bestResult[0]) > 0: #: averageResult.fScore > bestResult[1].fScore:
            bestResult = [evaluator, trainOutput, testOutput, evaluationOutput, combination]
        combinationCount += 1
    Stream.setIndent()
    print >> sys.stderr, "Selected parameters", bestResult[-1]
    return bestResult

def optimizeCSC(Classifier, Evaluator, trainExamples, testExamples, classIds, combinations, workDir=None, timeout=None, cscConnection=None, downloadAllModels=False):
    bestResult = None
    combinationCount = 1
    combinationIds = []
    print >> sys.stderr, "Initializing runs"
    for combination in combinations:
        Stream.setIndent(" ")
        print >> sys.stderr, "Parameters "+str(combinationCount)+"/"+str(len(combinations))+":", str(combination)
        # Train
        combinationIds.append(Classifier.initTrainAndTestOnLouhi(trainExamples, testExamples, combination, cscConnection) )
        combinationCount += 1

    print >> sys.stderr, "Waiting for results"
    finished = 0
    louhiTimer = Timer()
    combinationStatus = {}
    while(True):
        # count finished
        finished = 0
        processStatus = {"FINISHED":0, "QUEUED":0, "FAILED":0, "RUNNING":0}
        for id in combinationIds:
            status = Classifier.getLouhiStatus(id, cscConnection)
            combinationStatus[id] = status
            processStatus[status] += 1
        p = processStatus
        processStatusString = str(p["QUEUED"]) + " queued, " + str(p["RUNNING"]) + " running, " + str(p["FINISHED"]) + " finished, " + str(p["FAILED"]) + " failed"
        if processStatus["QUEUED"] + processStatus["RUNNING"] == 0:
            print >> sys.stderr
            print >> sys.stderr, "All runs done (" + processStatusString + ")"
            break
        # decide what to do
        if timeout == None or louhiTimer.getElapsedTime() < timeout:
            sleepString = " [          ]     "
            print >> sys.stderr, "\rWaiting for " + str(len(combinations)) + " on " + cscConnection.machineName + "(" + processStatusString + "),", louhiTimer.elapsedTimeToString() + sleepString,
            #time.sleep(60)
            sleepTimer = Timer()
            while sleepTimer.getElapsedTime() < 60:
                steps = int(10 * sleepTimer.getElapsedTime() / 60) + 1
                sleepString = " [" + steps * "." + (10-steps) * " " + "]     "
                print >> sys.stderr, "\rWaiting for " + str(len(combinations)) + " on " + cscConnection.machineName + "(" + processStatusString + "),", louhiTimer.elapsedTimeToString() + sleepString,
                time.sleep(5)                
        else:
            print >> sys.stderr
            print >> sys.stderr, "Timed out, ", louhiTimer.elapsedTimeToString()
            break
    
    print >> sys.stderr, "Evaluating results"
    #if type(testExamples) != types.ListType:
    #    print >> sys.stderr, "Loading examples from file", testExamples
    #    testExamples = ExampleUtils.readExamples(testExamples,False)
    bestCombinationId = None
    for i in range(len(combinationIds)):
        id = combinationIds[i]
        Stream.setIndent(" ")
        # Evaluate
        predictions = Classifier.getLouhiPredictions(id, cscConnection, workDir)
        if predictions == None:
            print >> sys.stderr, "No results for combination" + id
        else:
            if downloadAllModels:
                Classifier.downloadModel(id, cscConnection, workDir)
            print >> sys.stderr, "Evaluating results for combination" + id
            evaluationOutput = "evaluation" + id + ".csv"
            if workDir != None:
                evaluationOutput = os.path.join(workDir, evaluationOutput)
            evaluator = Evaluator.evaluate(testExamples, predictions, classIds, evaluationOutput)
            if bestResult == None or evaluator.compare(bestResult[0]) > 0: #: averageResult.fScore > bestResult[1].fScore:
                bestResult = [evaluator, None, predictions, evaluationOutput, combinations[i]]
                bestCombinationId = id
    Stream.setIndent()
    print >> sys.stderr, "Selected parameters", bestResult[-1]

    modelFileName = Classifier.downloadModel(bestCombinationId, cscConnection, workDir)
    if workDir != None:
        modelFileName = os.path.join(workDir, modelFileName)
    bestResult[1] = modelFileName
    return bestResult
